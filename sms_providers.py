"""短信服务商适配层（手机号验证码用）。

只发送「验证码」这一种短信。验证码由 phone_otp.py 生成、哈希保存和校验，这里只负责把码送出去。

选择服务商（环境变量，全部只在 Render 环境里设置，不入库）：
  SMS_PROVIDER      默认服务商：mock（默认）| aliyun | tencent | nhn | twilio
  SMS_PROVIDER_CN   +86 号码用哪家（不设则用 SMS_PROVIDER）
  SMS_PROVIDER_KR   +82 号码用哪家（不设则用 SMS_PROVIDER）

各家需要的环境变量见各 Provider 类的 docstring，以及 docs/PHONE_OTP.md。
本文件没有任何密钥；没配置齐全时 send_otp 抛 SmsError，不会静默失败。
所有真实服务商都只用 HTTPS 调官方接口，未联调（需要用户开通账号后再用真实密钥测一次）。
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional, Tuple
from urllib.parse import quote

log = logging.getLogger("sms")


class SmsError(Exception):
    """发送失败。message 只用于服务端日志，绝不包含验证码、密钥或完整手机号。"""


@dataclass
class SmsMessage:
    to_e164: str          # 例如 +8613812345678 / +821012345678
    country: str          # "86" / "82"
    national: str         # 不含国家码的号码：13812345678 / 1012345678
    code: str             # 6 位验证码
    ttl_minutes: int = 5


def mask_for_log(e164: str) -> str:
    return e164[:5] + "****" + e164[-3:] if len(e164) > 8 else "***"


# ---------------- HTTP（可注入，便于不联网测试） ----------------
HttpPost = Callable[..., Tuple[int, str]]


def default_http_post(url: str, *, headers: Optional[dict] = None, data: Optional[dict] = None,
                      content: Optional[bytes] = None, auth: Optional[tuple] = None, timeout: float = 10.0) -> Tuple[int, str]:
    import httpx
    r = httpx.post(url, headers=headers, data=data, content=content, auth=auth, timeout=timeout)
    return r.status_code, r.text


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _need(*names: str) -> dict:
    vals = {n: _env(n) for n in names}
    missing = [n for n, v in vals.items() if not v]
    if missing:
        raise SmsError("短信服务未配置完整，缺少环境变量：" + ", ".join(missing))
    return vals


def is_production() -> bool:
    return bool(os.environ.get("RENDER")) or _env("APP_ENV").lower() in ("prod", "production")


class SmsProvider:
    name = "base"

    def __init__(self, post: Optional[HttpPost] = None):
        self._post = post or default_http_post

    def send_otp(self, msg: SmsMessage) -> str:
        """发送成功返回服务商消息 ID（可为空串）；失败抛 SmsError。"""
        raise NotImplementedError


# ---------------- 1. Mock（默认） ----------------
class MockProvider(SmsProvider):
    """不发真实短信。开发环境把验证码打印到服务端日志；生产环境默认拒绝（避免开关打开却没配短信商）。
    测试用：MockProvider.outbox 保存最近的 (to, code)。仅在非生产环境记录。"""
    name = "mock"
    outbox: list = []

    def send_otp(self, msg: SmsMessage) -> str:
        if is_production() and _env("PHONE_OTP_ALLOW_MOCK_IN_PROD") != "1":
            raise SmsError("生产环境未配置短信服务商（SMS_PROVIDER 仍是 mock）")
        MockProvider.outbox.append((msg.to_e164, msg.code))
        del MockProvider.outbox[:-50]
        if not is_production():
            print("[MOCK-SMS] to=%s code=%s (dev only)" % (mask_for_log(msg.to_e164), msg.code), flush=True)
        return "mock"


# ---------------- 2. 阿里云（+86） ----------------
def _pct(s: str) -> str:
    return quote(str(s), safe="-_.~")


def aliyun_rpc_sign(params: dict, secret: str, method: str = "POST") -> str:
    """阿里云 RPC 签名 v1.0（HMAC-SHA1）：官方文档 https://help.aliyun.com/zh/sms/developer-reference/request-structure-and-signature"""
    canon = "&".join(_pct(k) + "=" + _pct(params[k]) for k in sorted(params))
    to_sign = method + "&" + _pct("/") + "&" + _pct(canon)
    return base64.b64encode(hmac.new((secret + "&").encode(), to_sign.encode(), hashlib.sha1).digest()).decode()


class AliyunSmsProvider(SmsProvider):
    """阿里云，只用于 +86。两种模式（ALIYUN_SMS_MODE）：
      pnvs（默认）：号码认证服务「短信认证」SendSmsVerifyCode，个人实名即可，用系统赠送签名+模板，不用企业资质；
                    需要 ALIYUN_SMS_SIGN_NAME（赠送签名名）、ALIYUN_SMS_TEMPLATE_CODE（赠送模板编号）。
      sms：短信服务 SendSms，需要企业资质、自建签名和验证码模板（模板变量名默认 code，可用 ALIYUN_SMS_CODE_PARAM 改）。
    共用：ALIYUN_ACCESS_KEY_ID、ALIYUN_ACCESS_KEY_SECRET（建议 RAM 子账号，只授权短信发送）。
    我们自己生成验证码并传给阿里云，所以校验由本站完成。"""
    name = "aliyun"

    def send_otp(self, msg: SmsMessage) -> str:
        if msg.country != "86":
            raise SmsError("阿里云只用于 +86 号码")
        c = _need("ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET", "ALIYUN_SMS_SIGN_NAME", "ALIYUN_SMS_TEMPLATE_CODE")
        mode = (_env("ALIYUN_SMS_MODE", "pnvs")).lower()
        param_name = _env("ALIYUN_SMS_CODE_PARAM", "code")
        tparam = {param_name: msg.code}
        if mode == "pnvs":
            host, action = "dypnsapi.aliyuncs.com", "SendSmsVerifyCode"
            tparam["min"] = str(msg.ttl_minutes)
            biz = {"PhoneNumber": msg.national, "CountryCode": "86", "SignName": c["ALIYUN_SMS_SIGN_NAME"],
                   "TemplateCode": c["ALIYUN_SMS_TEMPLATE_CODE"], "TemplateParam": json.dumps(tparam, separators=(",", ":")),
                   "ValidTime": str(msg.ttl_minutes * 60)}
        elif mode == "sms":
            host, action = "dysmsapi.aliyuncs.com", "SendSms"
            biz = {"PhoneNumbers": msg.national, "SignName": c["ALIYUN_SMS_SIGN_NAME"],
                   "TemplateCode": c["ALIYUN_SMS_TEMPLATE_CODE"], "TemplateParam": json.dumps(tparam, separators=(",", ":"))}
        else:
            raise SmsError("ALIYUN_SMS_MODE 只能是 pnvs 或 sms")
        params = {"Action": action, "Version": "2017-05-25", "Format": "JSON",
                  "AccessKeyId": c["ALIYUN_ACCESS_KEY_ID"], "SignatureMethod": "HMAC-SHA1", "SignatureVersion": "1.0",
                  "SignatureNonce": str(uuid.uuid4()),
                  "Timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **biz}
        params["Signature"] = aliyun_rpc_sign(params, c["ALIYUN_ACCESS_KEY_SECRET"])
        status, text = self._post("https://" + host + "/", data=params,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            j = json.loads(text)
        except Exception:
            raise SmsError("阿里云返回无法解析 http=%s" % status)
        if status != 200 or j.get("Code") != "OK" or j.get("Success") is False:
            raise SmsError("阿里云发送失败 http=%s code=%s" % (status, str(j.get("Code"))[:40]))
        return str(j.get("BizId") or (j.get("Model") or {}).get("BizId") or "")


# ---------------- 3. 腾讯云（+86 与国际/+82） ----------------
class TencentSmsProvider(SmsProvider):
    """腾讯云短信 SendSms（API 3.0，TC3-HMAC-SHA256）。
    必填：TENCENT_SMS_SECRET_ID、TENCENT_SMS_SECRET_KEY、TENCENT_SMS_SDK_APP_ID
    +86：TENCENT_SMS_SIGN_NAME、TENCENT_SMS_TEMPLATE_ID_CN（验证码模板，变量 {1}=验证码 {2}=有效分钟）
    +82：TENCENT_SMS_TEMPLATE_ID_INTL（国际/港澳台验证码模板，不需要签名；变量同上）
    可选：TENCENT_SMS_REGION（默认 ap-guangzhou）"""
    name = "tencent"
    host = "sms.tencentcloudapi.com"

    @staticmethod
    def sign_headers(secret_id, secret_key, payload: str, ts: int, region: str, host: str = "sms.tencentcloudapi.com") -> dict:
        date = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")
        ct = "application/json; charset=utf-8"
        canon_headers = "content-type:%s\nhost:%s\nx-tc-action:%s\n" % (ct, host, "sendsms")
        signed = "content-type;host;x-tc-action"
        canon = "POST\n/\n\n" + canon_headers + "\n" + signed + "\n" + hashlib.sha256(payload.encode()).hexdigest()
        scope = date + "/sms/tc3_request"
        to_sign = "TC3-HMAC-SHA256\n%d\n%s\n%s" % (ts, scope, hashlib.sha256(canon.encode()).hexdigest())

        def h(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
        k = h(h(h(("TC3" + secret_key).encode(), date), "sms"), "tc3_request")
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        auth = "TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (secret_id, scope, signed, sig)
        return {"Authorization": auth, "Content-Type": ct, "Host": host, "X-TC-Action": "SendSms",
                "X-TC-Timestamp": str(ts), "X-TC-Version": "2021-01-11", "X-TC-Region": region}

    def send_otp(self, msg: SmsMessage) -> str:
        c = _need("TENCENT_SMS_SECRET_ID", "TENCENT_SMS_SECRET_KEY", "TENCENT_SMS_SDK_APP_ID")
        body = {"PhoneNumberSet": [msg.to_e164], "SmsSdkAppId": c["TENCENT_SMS_SDK_APP_ID"],
                "TemplateParamSet": [msg.code, str(msg.ttl_minutes)]}
        if msg.country == "86":
            body["TemplateId"] = _need("TENCENT_SMS_TEMPLATE_ID_CN")["TENCENT_SMS_TEMPLATE_ID_CN"]
            body["SignName"] = _need("TENCENT_SMS_SIGN_NAME")["TENCENT_SMS_SIGN_NAME"]
        else:
            body["TemplateId"] = _need("TENCENT_SMS_TEMPLATE_ID_INTL")["TENCENT_SMS_TEMPLATE_ID_INTL"]
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        headers = self.sign_headers(c["TENCENT_SMS_SECRET_ID"], c["TENCENT_SMS_SECRET_KEY"], payload,
                                    int(time.time()), _env("TENCENT_SMS_REGION", "ap-guangzhou"), self.host)
        status, text = self._post("https://" + self.host + "/", headers=headers, content=payload.encode())
        try:
            r = json.loads(text)["Response"]
        except Exception:
            raise SmsError("腾讯云返回无法解析 http=%s" % status)
        if "Error" in r:
            raise SmsError("腾讯云发送失败 code=%s" % str(r["Error"].get("Code"))[:60])
        st = (r.get("SendStatusSet") or [{}])[0]
        if st.get("Code") != "Ok":
            raise SmsError("腾讯云发送失败 code=%s" % str(st.get("Code"))[:60])
        return str(st.get("SerialNo") or "")


# ---------------- 4. NHN Cloud（韩国 +82） ----------------
class NhnCloudSmsProvider(SmsProvider):
    """NHN Cloud Notification > SMS v3.0。
    必填：NHN_SMS_APP_KEY、NHN_SMS_SECRET_KEY、NHN_SMS_SEND_NO（必须是已在控制台「发信号码事先登记」并完成名义人认证的号码）。
    注意：NHN Cloud 的 SMS 只对已认证的企业（사업자）会员开放，个人会员不能用（官方文档，2023-12-15 起）。"""
    name = "nhn"

    def send_otp(self, msg: SmsMessage) -> str:
        c = _need("NHN_SMS_APP_KEY", "NHN_SMS_SECRET_KEY", "NHN_SMS_SEND_NO")
        body_text = _env("NHN_SMS_BODY_TEMPLATE", "[旅韩助手] 인증번호 {code} ({min}분 이내 입력). 타인에게 알려주지 마세요.").format(
            code=msg.code, min=msg.ttl_minutes)
        payload = json.dumps({"body": body_text, "sendNo": c["NHN_SMS_SEND_NO"],
                              "recipientList": [{"recipientNo": ("0" + msg.national) if msg.country == "82" else msg.national,
                                                 "countryCode": msg.country}]}, ensure_ascii=False)
        url = "https://sms.api.nhncloudservice.com/sms/v3.0/appKeys/%s/sender/sms" % c["NHN_SMS_APP_KEY"]
        status, text = self._post(url, headers={"Content-Type": "application/json;charset=UTF-8",
                                                "X-Secret-Key": c["NHN_SMS_SECRET_KEY"]}, content=payload.encode())
        try:
            j = json.loads(text)
        except Exception:
            raise SmsError("NHN 返回无法解析 http=%s" % status)
        hd = j.get("header") or {}
        if status != 200 or not hd.get("isSuccessful"):
            raise SmsError("NHN 发送失败 http=%s code=%s" % (status, str(hd.get("resultCode"))[:20]))
        return str(((j.get("body") or {}).get("data") or {}).get("requestId") or "")


# ---------------- 5. Twilio（国际，+82 / +86 都可） ----------------
class TwilioSmsProvider(SmsProvider):
    """Twilio Programmable Messaging。必填：TWILIO_ACCOUNT_SID、TWILIO_AUTH_TOKEN，
    以及 TWILIO_MESSAGING_SERVICE_SID 或 TWILIO_FROM（二选一）。个人账号可注册；
    发往中国大陆的短信需要提前做发件人/模板合规登记，成功率低，不建议用于 +86。"""
    name = "twilio"

    def send_otp(self, msg: SmsMessage) -> str:
        c = _need("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
        data = {"To": msg.to_e164, "Body": _env("TWILIO_BODY_TEMPLATE", "[LvHan] Your verification code is {code}. Valid for {min} minutes. Do not share it.").format(
            code=msg.code, min=msg.ttl_minutes)}
        if _env("TWILIO_MESSAGING_SERVICE_SID"):
            data["MessagingServiceSid"] = _env("TWILIO_MESSAGING_SERVICE_SID")
        elif _env("TWILIO_FROM"):
            data["From"] = _env("TWILIO_FROM")
        else:
            raise SmsError("短信服务未配置完整，缺少 TWILIO_MESSAGING_SERVICE_SID 或 TWILIO_FROM")
        url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % c["TWILIO_ACCOUNT_SID"]
        status, text = self._post(url, data=data, auth=(c["TWILIO_ACCOUNT_SID"], c["TWILIO_AUTH_TOKEN"]))
        try:
            j = json.loads(text)
        except Exception:
            raise SmsError("Twilio 返回无法解析 http=%s" % status)
        if status not in (200, 201) or j.get("error_code"):
            raise SmsError("Twilio 发送失败 http=%s code=%s" % (status, str(j.get("code") or j.get("error_code"))[:20]))
        return str(j.get("sid") or "")


PROVIDERS = {"mock": MockProvider, "aliyun": AliyunSmsProvider, "tencent": TencentSmsProvider,
             "nhn": NhnCloudSmsProvider, "twilio": TwilioSmsProvider}


def get_provider(country: str, post: Optional[HttpPost] = None) -> SmsProvider:
    default = _env("SMS_PROVIDER", "mock").lower()
    key = _env("SMS_PROVIDER_CN" if country == "86" else "SMS_PROVIDER_KR", default).lower() or default
    cls = PROVIDERS.get(key)
    if not cls:
        raise SmsError("未知的短信服务商：" + key[:20])
    return cls(post=post)
