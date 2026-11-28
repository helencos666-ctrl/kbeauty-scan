"""手机号绑定 + 短信验证码：python test/test_phone_otp.py
使用本机 Postgres 独立测试库（lvhan_phoneotp_v1），不联网、不发真实短信、不打印连接串或密钥。"""
import datetime
import hashlib
import json
import os
import random
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:400])


def unit_tests():
    """纯函数：号码规范化 / 打码 / 服务商请求构造（假 HTTP，不联网）"""
    from fastapi import HTTPException
    import phone_otp as PO
    import sms_providers as S

    check("norm cn", PO.normalize_phone("86", "138 1234 5678") == ("86", "13812345678", "+8613812345678"))
    check("norm cn +86 prefix", PO.normalize_phone("", "+86 138-1234-5678")[2] == "+8613812345678")
    check("norm cn 0086", PO.normalize_phone("86", "008613812345678")[2] == "+8613812345678")
    check("norm kr 010", PO.normalize_phone("82", "010-1234-5678")[2] == "+821012345678")
    check("norm kr no zero", PO.normalize_phone("82", "1012345678")[2] == "+821012345678")
    check("norm kr +82", PO.normalize_phone("", "+82 10 1234 5678")[2] == "+821012345678")
    check("norm kr 9-digit 011", PO.normalize_phone("82", "011-123-4567")[2] == "+82111234567")
    for c, p in (("86", "12345678901"), ("86", "1381234"), ("82", "02-123-4567"), ("82", "010123"), ("1", "2025550100"), ("", "abc"), ("86", "")):
        try:
            PO.normalize_phone(c, p)
            check("norm rejects %s %s" % (c, p), False)
        except HTTPException as e:
            check("norm rejects %s %s" % (c, p), e.status_code == 400)
    check("mask cn", PO.mask_phone("+8613812345678") == "+86 138****5678", PO.mask_phone("+8613812345678"))
    check("mask kr", PO.mask_phone("+821012345678") == "+82 10-****-5678", PO.mask_phone("+821012345678"))
    check("mask hides middle", "1234" not in PO.mask_phone("+8613812345678") and "1234" not in PO.mask_phone("+821012345678"))
    png = PO.make_captcha_image("2468")
    check("captcha png", png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 500)

    # ---- 服务商请求构造 ----
    calls = []

    def fake(resp_text, status=200):
        def post(url, **kw):
            calls.append((url, kw))
            return status, resp_text
        return post

    msg = S.SmsMessage("+8613812345678", "86", "13812345678", "123456", 5)
    kr = S.SmsMessage("+821012345678", "82", "1012345678", "654321", 5)
    env = dict(os.environ)
    try:
        # aliyun pnvs
        os.environ.update(ALIYUN_ACCESS_KEY_ID="AKID_TEST", ALIYUN_ACCESS_KEY_SECRET="secret_test", ALIYUN_SMS_SIGN_NAME="签名", ALIYUN_SMS_TEMPLATE_CODE="100001")
        p = S.AliyunSmsProvider(post=fake(json.dumps({"Code": "OK", "Success": True, "Model": {"BizId": "b1"}})))
        check("aliyun pnvs ok", p.send_otp(msg) == "b1")
        url, kw = calls[-1]
        d = kw["data"]
        check("aliyun pnvs endpoint/action", "dypnsapi.aliyuncs.com" in url and d["Action"] == "SendSmsVerifyCode" and d["PhoneNumber"] == "13812345678")
        check("aliyun param has code", json.loads(d["TemplateParam"])["code"] == "123456")
        sig = d.pop("Signature")
        check("aliyun signature recomputes", S.aliyun_rpc_sign(d, "secret_test") == sig and len(sig) > 20)
        check("aliyun secret not in request", "secret_test" not in json.dumps(d))
        d["PhoneNumber"] = "13900000000"
        check("aliyun signature changes with params", S.aliyun_rpc_sign(d, "secret_test") != sig)
        os.environ["ALIYUN_SMS_MODE"] = "sms"
        S.AliyunSmsProvider(post=fake(json.dumps({"Code": "OK"}))).send_otp(msg)
        check("aliyun sms mode", "dysmsapi.aliyuncs.com" in calls[-1][0] and calls[-1][1]["data"]["Action"] == "SendSms")
        os.environ.pop("ALIYUN_SMS_MODE")
        try:
            S.AliyunSmsProvider(post=fake(json.dumps({"Code": "isv.BUSINESS_LIMIT_CONTROL"}))).send_otp(msg)
            check("aliyun error raises", False)
        except S.SmsError as e:
            check("aliyun error raises, no code leak", "123456" not in str(e))
        try:
            S.AliyunSmsProvider(post=fake("{}")).send_otp(kr)
            check("aliyun rejects +82", False)
        except S.SmsError:
            check("aliyun rejects +82", True)
        # tencent
        os.environ.update(TENCENT_SMS_SECRET_ID="sid", TENCENT_SMS_SECRET_KEY="skey", TENCENT_SMS_SDK_APP_ID="1400000", TENCENT_SMS_SIGN_NAME="签名",
                          TENCENT_SMS_TEMPLATE_ID_CN="111", TENCENT_SMS_TEMPLATE_ID_INTL="222")
        p = S.TencentSmsProvider(post=fake(json.dumps({"Response": {"SendStatusSet": [{"Code": "Ok", "SerialNo": "s1"}]}})))
        check("tencent cn ok", p.send_otp(msg) == "s1")
        body = json.loads(calls[-1][1]["content"])
        h = calls[-1][1]["headers"]
        check("tencent cn body", body["TemplateId"] == "111" and body["SignName"] == "签名" and body["PhoneNumberSet"] == ["+8613812345678"] and body["TemplateParamSet"] == ["123456", "5"])
        check("tencent auth header", h["Authorization"].startswith("TC3-HMAC-SHA256 Credential=sid/") and h["X-TC-Action"] == "SendSms" and "skey" not in json.dumps(h))
        p.send_otp(kr)
        body = json.loads(calls[-1][1]["content"])
        check("tencent intl uses intl template, no sign", body["TemplateId"] == "222" and "SignName" not in body)
        # TC3 签名：同输入固定输出
        h1 = S.TencentSmsProvider.sign_headers("sid", "skey", "{}", 1700000000, "ap-guangzhou")
        h2 = S.TencentSmsProvider.sign_headers("sid", "skey", "{}", 1700000000, "ap-guangzhou")
        check("tencent sign deterministic", h1["Authorization"] == h2["Authorization"])
        try:
            S.TencentSmsProvider(post=fake(json.dumps({"Response": {"Error": {"Code": "FailedOperation.X"}}}))).send_otp(msg)
            check("tencent error raises", False)
        except S.SmsError:
            check("tencent error raises", True)
        # nhn
        os.environ.update(NHN_SMS_APP_KEY="appk", NHN_SMS_SECRET_KEY="nsecret", NHN_SMS_SEND_NO="0212345678")
        p = S.NhnCloudSmsProvider(post=fake(json.dumps({"header": {"isSuccessful": True}, "body": {"data": {"requestId": "r1"}}})))
        check("nhn ok", p.send_otp(kr) == "r1")
        url, kw = calls[-1]
        body = json.loads(kw["content"])
        check("nhn request", url.startswith("https://sms.api.nhncloudservice.com/sms/v3.0/appKeys/appk/sender/sms") and kw["headers"]["X-Secret-Key"] == "nsecret"
              and body["recipientList"][0]["recipientNo"] == "01012345678" and body["recipientList"][0]["countryCode"] == "82" and "654321" in body["body"])
        # twilio
        os.environ.update(TWILIO_ACCOUNT_SID="ACtest", TWILIO_AUTH_TOKEN="tok", TWILIO_FROM="+15005550006")
        p = S.TwilioSmsProvider(post=fake(json.dumps({"sid": "SM1"}), 201))
        check("twilio ok", p.send_otp(kr) == "SM1")
        url, kw = calls[-1]
        check("twilio request", url.endswith("/Accounts/ACtest/Messages.json") and kw["data"]["To"] == "+821012345678" and kw["auth"] == ("ACtest", "tok") and "654321" in kw["data"]["Body"])
        # 缺配置
        for k in ("NHN_SMS_APP_KEY",):
            os.environ.pop(k)
        try:
            S.NhnCloudSmsProvider(post=fake("{}")).send_otp(kr)
            check("missing env raises", False)
        except S.SmsError as e:
            check("missing env raises, names var only", "NHN_SMS_APP_KEY" in str(e) and "nsecret" not in str(e))
        # 路由
        os.environ.update(SMS_PROVIDER="mock", SMS_PROVIDER_CN="aliyun", SMS_PROVIDER_KR="twilio")
        check("router cn/kr", S.get_provider("86").name == "aliyun" and S.get_provider("82").name == "twilio")
        os.environ.pop("SMS_PROVIDER_CN"); os.environ.pop("SMS_PROVIDER_KR")
        check("router default mock", S.get_provider("86").name == "mock")
        # mock：生产环境默认拒绝
        os.environ["RENDER"] = "true"
        try:
            S.MockProvider().send_otp(msg)
            check("mock refused in prod", False)
        except S.SmsError:
            check("mock refused in prod", True)
        os.environ.pop("RENDER")
    finally:
        os.environ.clear()
        os.environ.update(env)


def main():
    unit_tests()
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_phoneotp_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_phoneotp_v1"
    if not dbname.isidentifier() or not dbname.isascii():
        raise RuntimeError("bad test db name")
    try:
        import psycopg
        admin_db = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
        admin_db.autocommit = True
        with admin_db.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (dbname,))
            cur.execute("DROP DATABASE IF EXISTS " + dbname)
            cur.execute("CREATE DATABASE " + dbname)
        admin_db.close()
    except Exception as e:
        print("SKIP db tests: no local postgres (%s)" % str(e)[:80])
        print("RESULT", "ok", ok, "fail", fail)
        return 0 if fail == 0 else 1

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import community as C
    import phone_otp as PO
    import sms_providers as S

    keys = ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD", "PHONE_OTP_REQUIRED", "PHONE_OTP_ENFORCE_FROM", "PHONE_OTP_CAPTCHA",
            "SMS_PROVIDER", "PHONE_OTP_PHONE_DAILY", "PHONE_OTP_IP_HOURLY", "PHONE_OTP_IP_DAILY", "PHONE_OTP_GLOBAL_DAILY", "WECHAT_MP_APPID", "WECHAT_MP_SECRET")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    for k in ("PHONE_OTP_REQUIRED", "PHONE_OTP_ENFORCE_FROM", "PHONE_OTP_CAPTCHA", "SMS_PROVIDER"):
        os.environ.pop(k, None)
    C._schema_ready = False
    C._psycopg = None
    app = FastAPI()
    app.include_router(C.router)
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)
    ipn = [0]

    def newcli():
        ipn[0] += 1
        c = TestClient(app)
        c.headers.update({"CF-Connecting-IP": "203.0.113.%d" % (ipn[0] % 250 + 1)})
        return c

    def dbq(sql, args=()):
        import psycopg
        with psycopg.connect(dsn) as cn:
            cur = cn.cursor()
            cur.execute(sql, args)
            rows = cur.fetchall() if cur.description else None
            cn.commit()
            return rows

    def last_code(e164):
        for to, code in reversed(S.MockProvider.outbox):
            if to == e164:
                return code
        return None

    def send(cli, country, phone, purpose="register", **extra):
        return cli.post("/api/community/phone/send", json=dict({"country": country, "phone": phone, "purpose": purpose}, **extra), headers=H)

    def age_otp(e164, seconds=120):
        dbq("UPDATE c_phone_otp SET created_at=created_at - make_interval(secs => %s), expires_at=expires_at - make_interval(secs => %s) WHERE phone=%s",
            (seconds, seconds, e164))

    try:
        # ======================= A. 开关关闭 = 旧行为不变 =======================
        a = newcli()
        check("A flag off: enabled false", a.get("/api/community/phone/config").json()["enabled"] is False)
        r = send(a, "86", "13800000001")
        check("A flag off: send 404", r.status_code == 404, r.status_code)
        check("A flag off: captcha 404", a.get("/api/community/phone/captcha").status_code == 404)
        check("A flag off: bind 404", a.post("/api/community/phone/bind", json={}, headers=H).status_code == 404)
        check("A flag off: wx-bind 404", a.post("/api/mp/phone/wx-bind", json={"code": "x"}, headers=H).status_code == 404)
        check("A flag off: /phone-bind page 404", a.get("/phone-bind").status_code == 404)
        r = a.post("/api/community/register", json={"account": "off%d@example.com" % rnd, "password": "Passw0rd!xyz", "nickname": "关闭态", "agree": True}, headers=H)
        check("A flag off: email register without phone ok", r.status_code == 200 and r.json() == {"ok": True}, r.text)
        me = a.get("/api/community/me").json()["user"]
        check("A flag off: /me has no phone key", "phone" not in me, list(me.keys()))
        check("A flag off: /api/me/summary has no phone key", "phone" not in a.get("/api/me/summary").json()["user"])
        b = newcli()
        r = b.post("/api/community/register", json={"account": "13800000002", "password": "Passw0rd!xyz", "nickname": "老式手机", "agree": True}, headers=H)
        check("A flag off: phone register without code ok (old behaviour)", r.status_code == 200, r.text)
        row = dbq("SELECT phone, phone_verified_at FROM c_users WHERE phone='+8613800000002'")
        check("A flag off: stored E.164, not verified", row and row[0][1] is None, row)
        os.environ["PHONE_OTP_ENFORCE_FROM"] = "2020-01-01"      # 即使设了日期，开关关闭也不拦任何人
        r = b.post("/api/me/profile", json={"nickname": "老式手机乙", "bio": ""}, headers=H)
        check("A flag off: enforcement date ignored", r.status_code == 200, r.text)
        os.environ.pop("PHONE_OTP_ENFORCE_FROM")
        r = newcli().post("/api/community/login", json={"account": "13800000002", "password": "Passw0rd!xyz"}, headers=H)
        check("A flag off: login response unchanged", r.json() == {"ok": True}, r.text)
        legacy_email = "off%d@example.com" % rnd

        # ======================= B. 开关打开 =======================
        os.environ["PHONE_OTP_REQUIRED"] = "1"
        os.environ["PHONE_OTP_CAPTCHA"] = "off"
        S.MockProvider.outbox.clear()
        cfgj = newcli().get("/api/community/phone/config").json()
        check("B config enabled + countries", cfgj["enabled"] and {c["code"] for c in cfgj["countries"]} == {"86", "82"}, cfgj)
        check("B page /phone-bind 200", newcli().get("/phone-bind").status_code == 200)

        g = newcli()
        reg_body = {"password": "Passw0rd!xyz", "nickname": "新用户甲", "agree": True, "country": "86", "phone": "138 0000 0010"}
        r = g.post("/api/community/register", json=reg_body, headers=H)
        check("B register without code rejected", r.status_code == 400 and "验证码" in r.text, r.text)
        r = g.post("/api/community/register", json=dict(reg_body, phone_code="123456"), headers=H)
        check("B register with unsent code rejected", r.status_code == 400, r.text)
        check("B no account created yet", dbq("SELECT count(*) FROM c_users WHERE phone='+8613800000010'")[0][0] == 0)
        r = g.post("/api/community/register", json={"password": "Passw0rd!xyz", "nickname": "无手机", "agree": True, "account": "x%d@example.com" % rnd}, headers=H)
        check("B email-only register rejected when flag on", r.status_code == 400, r.text)
        r = send(g, "86", "138 0000 0010")
        check("B send ok + masked", r.status_code == 200 and r.json()["masked"] == "+86 138****0010" and r.json()["cooldown"] == 60 and r.json()["expires_in"] == 300, r.text)
        code = last_code("+8613800000010")
        check("B mock got 6-digit code", code and len(code) == 6 and code.isdigit(), code)
        rows = dbq("SELECT code_hash, salt, expires_at-created_at FROM c_phone_otp WHERE phone='+8613800000010'")
        check("B code hashed at rest (not plaintext)", rows and rows[0][0] != code and code not in rows[0][0] and len(rows[0][0]) == 64)
        check("B expiry is 5 minutes", rows[0][2] == datetime.timedelta(minutes=5), rows[0][2])
        r = send(g, "86", "138 0000 0010")
        check("B resend within 60s -> 429 with wait", r.status_code == 429 and "秒" in r.text, r.text)
        wrong = "000000" if code != "000000" else "111111"
        r = g.post("/api/community/register", json=dict(reg_body, phone_code=wrong), headers=H)
        check("B wrong code rejected, shows remaining", r.status_code == 400 and "4" in r.text, r.text)
        check("B still no account after wrong code", dbq("SELECT count(*) FROM c_users WHERE phone='+8613800000010'")[0][0] == 0)
        r = g.post("/api/community/register", json=dict(reg_body, phone_code=code), headers=H)
        check("B register with right code ok", r.status_code == 200, r.text)
        row = dbq("SELECT phone, email, phone_verified_at IS NOT NULL, phone_verify_method FROM c_users WHERE phone='+8613800000010'")
        check("B account stored E.164 + verified", row == [("+8613800000010", None, True, "sms")], row)
        me = g.get("/api/community/me").json()["user"]
        check("B /me phone masked + verified", me["phone"]["verified"] is True and me["phone"]["masked"] == "+86 138****0010" and "13800000010" not in json.dumps(me), me.get("phone"))
        check("B /me.account masked (existing behaviour)", "13800000010" not in me["account"])
        r = newcli().post("/api/community/register", json=dict(reg_body, nickname="盗用码", phone_code=code), headers=H)
        check("B code is single-use (replay rejected)", r.status_code in (400, 409), r.text)

        # --- 一号一账号 ---
        d = newcli()
        r = send(d, "86", "13800000010")
        check("B duplicate phone: send rejected 409", r.status_code == 409, r.text)
        r = d.post("/api/community/register", json=dict(reg_body, nickname="重复", phone_code="123456"), headers=H)
        check("B duplicate phone: register rejected", r.status_code in (400, 409), r.text)
        # +82 号码注册（带邮箱账号）
        k = newcli()
        r = send(k, "82", "010-5555-0001")
        check("B kr send ok", r.status_code == 200 and r.json()["masked"] == "+82 10-****-0001", r.text)
        kcode = last_code("+821055550001")
        r = k.post("/api/community/register", json={"account": "kr%d@example.com" % rnd, "password": "Passw0rd!xyz", "nickname": "韩国号", "agree": True,
                                                    "country": "82", "phone": "01055550001", "phone_code": kcode}, headers=H)
        check("B kr register with email account ok", r.status_code == 200, r.text)
        row = dbq("SELECT phone, email, phone_verified_at IS NOT NULL FROM c_users WHERE phone='+821055550001'")
        check("B kr stored +82, email kept", row and row[0][0] == "+821055550001" and row[0][1] == "kr%d@example.com" % rnd and row[0][2], row)
        r = newcli().post("/api/community/login", json={"account": "010-5555-0001", "password": "Passw0rd!xyz"}, headers=H)
        check("B can login with +82 phone", r.status_code == 200 and r.json()["phone"]["verified"] is True, r.text)
        r = newcli().post("/api/community/login", json={"account": "kr%d@example.com" % rnd, "password": "Passw0rd!xyz"}, headers=H)
        check("B can login with email", r.status_code == 200, r.text)
        # 账号手机号与验证手机号不一致
        m = newcli()
        send(m, "86", "13800000011")
        r = m.post("/api/community/register", json={"account": "13800000099", "password": "Passw0rd!xyz", "nickname": "错位", "agree": True, "country": "86",
                                                    "phone": "13800000011", "phone_code": last_code("+8613800000011")}, headers=H)
        check("B account/phone mismatch rejected", r.status_code == 400, r.text)

        # --- 错误 5 次锁定 ---
        t = newcli()
        send(t, "86", "13800000012")
        tcode = last_code("+8613800000012")
        bad = "000000" if tcode != "000000" else "111111"
        body12 = {"password": "Passw0rd!xyz", "nickname": "锁定测试", "agree": True, "country": "86", "phone": "13800000012"}
        codes = []
        for i in range(5):
            rr = t.post("/api/community/register", json=dict(body12, phone_code=bad), headers=H)
            codes.append(rr.status_code)
        check("B 5 wrong tries all rejected", codes == [400] * 5, codes)
        r = t.post("/api/community/register", json=dict(body12, phone_code=tcode), headers=H)
        check("B right code after 5 wrong is rejected (locked)", r.status_code in (400, 429), r.text)
        check("B no account after lockout", dbq("SELECT count(*) FROM c_users WHERE phone='+8613800000012'")[0][0] == 0)
        age_otp("+8613800000012", 61)
        r = send(t, "86", "13800000012")
        check("B can get new code after cooldown", r.status_code == 200, r.text)
        r = t.post("/api/community/register", json=dict(body12, phone_code=last_code("+8613800000012")), headers=H)
        check("B new code after lockout works", r.status_code == 200, r.text)

        # --- 过期 ---
        e = newcli()
        send(e, "86", "13800000013")
        ecode = last_code("+8613800000013")
        age_otp("+8613800000013", 301)
        r = e.post("/api/community/register", json=dict(body12, phone="13800000013", phone_code=ecode), headers=H)
        check("B expired code rejected", r.status_code == 400 and "过期" in r.text, r.text)

        # --- 重新发送后旧码作废 ---
        o = newcli()
        send(o, "86", "13800000014")
        c1 = last_code("+8613800000014")
        age_otp("+8613800000014", 61)
        send(o, "86", "13800000014")
        c2 = last_code("+8613800000014")
        if c1 != c2:
            r = o.post("/api/community/register", json=dict(body12, phone="13800000014", phone_code=c1), headers=H)
            check("B old code invalid after resend", r.status_code == 400, r.text)
        r = o.post("/api/community/register", json=dict(body12, phone="13800000014", phone_code=c2), headers=H)
        check("B latest code works", r.status_code == 200, r.text)

        # --- 每号每天上限 ---
        os.environ["PHONE_OTP_PHONE_DAILY"] = "3"
        p = newcli()
        res = []
        for i in range(4):
            res.append(send(p, "86", "13800000015").status_code)
            age_otp("+8613800000015", 61)
        check("B per-phone daily cap (3) -> 4th is 429", res == [200, 200, 200, 429], res)
        os.environ.pop("PHONE_OTP_PHONE_DAILY")

        # --- 每 IP 每小时上限 ---
        os.environ["PHONE_OTP_IP_HOURLY"] = "3"
        q1 = newcli()
        q1.headers.update({"CF-Connecting-IP": "198.51.100.77"})
        res = [send(q1, "86", "1380000002%d" % i).status_code for i in range(5)]
        check("B per-IP hourly cap (3)", res[:3] == [200] * 3 and res[3] == 429, res)
        os.environ.pop("PHONE_OTP_IP_HOURLY")
        os.environ["PHONE_OTP_IP_DAILY"] = "2"
        q2 = newcli()
        q2.headers.update({"CF-Connecting-IP": "198.51.100.78"})
        res = [send(q2, "86", "1380000003%d" % i).status_code for i in range(4)]
        check("B per-IP daily cap (2)", res[:2] == [200, 200] and res[2] == 429, res)
        os.environ.pop("PHONE_OTP_IP_DAILY")
        os.environ["PHONE_OTP_GLOBAL_DAILY"] = "1"
        res = send(newcli(), "86", "13800000040")
        check("B global daily cap -> 503", res.status_code == 503, res.status_code)
        os.environ.pop("PHONE_OTP_GLOBAL_DAILY")
        check("B ip stored hashed (no raw IP)", "198.51.100.77" not in json.dumps(dbq("SELECT ip_hash FROM c_phone_otp")))

        # --- 发送失败不占额度 + 不泄露 ---
        class Boom(S.SmsProvider):
            name = "boom"
            def send_otp(self, msg):
                raise S.SmsError("upstream down")
        orig = S.get_provider
        S.get_provider = lambda country, post=None: Boom()
        f = newcli()
        r = send(f, "86", "13800000050")
        S.get_provider = orig
        check("B provider failure -> 503, generic message", r.status_code == 503 and "upstream" not in r.text, r.text)
        check("B failed send leaves no OTP row (quota not burned)", dbq("SELECT count(*) FROM c_phone_otp WHERE phone='+8613800000050'")[0][0] == 0)
        r = send(f, "86", "13800000050")
        check("B retry after failure works immediately", r.status_code == 200, r.text)

        # --- 图形验证码（默认 simple） ---
        os.environ.pop("PHONE_OTP_CAPTCHA")
        capt = []
        orig_img = PO.make_captcha_image
        PO.make_captcha_image = lambda text: (capt.append(text) or b"\x89PNG-fake")
        cc = newcli()
        r = send(cc, "86", "13800000060")
        check("B captcha: send without captcha rejected", r.status_code == 400 and "图形验证码" in r.text, r.text)
        check("B captcha: no SMS sent without captcha", last_code("+8613800000060") is None)
        cj = cc.get("/api/community/phone/captcha").json()
        check("B captcha: image returned", cj["image"].startswith("data:image/png;base64,") and cj["id"], cj)
        r = send(cc, "86", "13800000060", captcha_id=cj["id"], captcha_answer="0000")
        check("B captcha: wrong answer rejected", r.status_code == 400, r.text)
        r = send(cc, "86", "13800000060", captcha_id=cj["id"], captcha_answer=capt[-1])
        check("B captcha: one-time (wrong guess burns it)", r.status_code == 400, r.text)
        cj = cc.get("/api/community/phone/captcha").json()
        r = send(cc, "86", "13800000060", captcha_id=cj["id"], captcha_answer=capt[-1])
        check("B captcha: right answer sends SMS", r.status_code == 200 and last_code("+8613800000060"), r.text)
        r = send(cc, "86", "13800000061", captcha_id=cj["id"], captcha_answer=capt[-1])
        check("B captcha: cannot reuse solved captcha", r.status_code == 400, r.text)
        check("B captcha stored hashed", capt[-1] not in json.dumps(dbq("SELECT answer_hash FROM c_phone_captcha")))
        # 自定义滑块钩子
        PO.register_captcha_provider("slider-test", lambda req, body: body.get("captcha_token") == "ok", {"app": "x"})
        os.environ["PHONE_OTP_CAPTCHA"] = "slider-test"
        r = send(cc, "86", "13800000062", captcha_token="bad")
        check("B captcha hook: bad token rejected", r.status_code == 400, r.text)
        cc.headers.update({"CF-Connecting-IP": "198.51.100.90"})
        r = send(cc, "86", "13800000062", captcha_token="ok")
        check("B captcha hook: ok token sends", r.status_code == 200, r.text)
        check("B captcha hook: client config exposed", cc.get("/api/community/phone/config").json()["captcha"]["type"] == "slider-test")
        PO.make_captcha_image = orig_img
        os.environ["PHONE_OTP_CAPTCHA"] = "off"

        # ======================= C. 老账号：软提示 -> 绑定 =======================
        # 老 email 账号（没有手机号）
        old = newcli()
        r = old.post("/api/community/login", json={"account": legacy_email, "password": "Passw0rd!xyz"}, headers=H)
        check("C legacy login ok, never blocked", r.status_code == 200 and r.json()["phone"]["verified"] is False and r.json()["phone"]["required_now"] is False, r.text)
        me = old.get("/api/community/me").json()["user"]
        check("C /me prompts soft (not required)", me["phone"]["verified"] is False and me["phone"]["required_now"] is False and me["phone"]["enforce_from"] is None, me["phone"])
        check("C /api/me/summary carries phone status", old.get("/api/me/summary").json()["user"]["phone"]["verified"] is False)
        r = send(old, "86", "13800000070", purpose="bind")
        check("C bind send ok", r.status_code == 200, r.text)
        bcode = last_code("+8613800000070")
        r = old.post("/api/community/phone/bind", json={"country": "86", "phone": "13800000070", "code": "000000" if bcode != "000000" else "111111"}, headers=H)
        check("C bind wrong code rejected", r.status_code == 400, r.text)
        # 别人的号码被占用
        r = send(old, "86", "13800000010", purpose="bind")
        check("C bind phone held by another account -> 409", r.status_code == 409, r.text)
        r = old.post("/api/community/phone/bind", json={"country": "86", "phone": "13800000070", "code": bcode}, headers=H)
        check("C bind ok", r.status_code == 200 and r.json()["phone"] == "+86 138****0070", r.text)
        row = dbq("SELECT phone, phone_verified_at IS NOT NULL, phone_verify_method FROM c_users WHERE email=%s", (legacy_email,))
        check("C bound: E.164 + verified + sms", row == [("+8613800000070", True, "sms")], row)
        check("C bound: /me verified", old.get("/api/community/me").json()["user"]["phone"]["verified"] is True)
        r = send(old, "86", "13800000071", purpose="bind")
        check("C already verified: cannot rebind via send", r.status_code == 400, r.text)
        r = old.post("/api/community/phone/bind", json={"country": "86", "phone": "13800000071", "code": "123456"}, headers=H)
        check("C already verified: cannot rebind", r.status_code == 400, r.text)
        check("C bind needs login", send(newcli(), "86", "13800000072", purpose="bind").status_code == 401)
        check("C bind needs csrf header", old.post("/api/community/phone/send", json={"country": "86", "phone": "13800000072", "purpose": "register"}).status_code == 403)

        # 老手机号账号（未验证）：就用自己的号码验证
        lp = newcli()
        r = lp.post("/api/community/login", json={"account": "13800000002", "password": "Passw0rd!xyz"}, headers=H)
        check("C legacy phone-account login: prompt, not blocked", r.status_code == 200 and r.json()["phone"]["verified"] is False, r.text)
        r = send(lp, "86", "13800000002", purpose="bind")
        check("C legacy phone-account: can verify its own number", r.status_code == 200, r.text)
        r = lp.post("/api/community/phone/bind", json={"country": "86", "phone": "13800000002", "code": last_code("+8613800000002")}, headers=H)
        check("C legacy phone-account verified", r.status_code == 200, r.text)
        # 老账号想绑定被其它老账号（未验证）占着的号码 -> 拒绝，不会抢占
        dbq("INSERT INTO c_users(phone,nickname,pw_hash) VALUES ('+8613800000080','占号者','x')")
        r = send(old2 := newcli(), "86", "13800000080", purpose="bind")
        check("C bind needs login (squatter case)", r.status_code == 401)

        # ======================= D. 到期强制：只拦写操作，不锁人 =======================
        un = newcli()
        un.post("/api/community/register", json={"account": "d%d@example.com" % rnd, "password": "Passw0rd!xyz", "nickname": "待绑定", "agree": True}, headers=H)  # 开关开着，应失败
        dbq("INSERT INTO c_users(email,nickname,pw_hash) VALUES (%s,'强制老用户',%s)", ("d%d@example.com" % rnd, C.hash_pw("Passw0rd!xyz")))
        r = un.post("/api/community/login", json={"account": "d%d@example.com" % rnd, "password": "Passw0rd!xyz"}, headers=H)
        check("D login ok before enforcement", r.status_code == 200 and r.json()["phone"]["required_now"] is False, r.text)
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        os.environ["PHONE_OTP_ENFORCE_FROM"] = future
        ph = un.get("/api/community/me").json()["user"]["phone"]
        check("D before deadline: countdown shown, not required", ph["required_now"] is False and ph["enforce_from"] == future and 28 <= ph["days_left"] <= 30, ph)
        r = un.post("/api/community/password", json={"old": "x", "new": "y"}, headers=H)
        check("D before deadline: writes not blocked by phone rule", "绑定手机号" not in r.text, r.text)
        os.environ["PHONE_OTP_ENFORCE_FROM"] = "2020-01-01"
        ph = un.get("/api/community/me").json()["user"]["phone"]
        check("D after deadline: required_now true", ph["required_now"] is True and ph["days_left"] == 0, ph)
        r = un.post("/api/me/profile", json={"nickname": "新昵称甲", "bio": ""}, headers=H)
        check("D after deadline: write blocked with clear message", r.status_code == 403 and "绑定手机号" in r.text, r.text)
        r = un.post("/api/community/report", json={"type": "post", "id": 1, "reason": "x"}, headers=H)
        check("D after deadline: other write endpoints blocked too", r.status_code in (403, 401) and ("绑定手机号" in r.text or r.status_code == 401), r.text)
        check("D after deadline: login still works", newcli().post("/api/community/login", json={"account": "d%d@example.com" % rnd, "password": "Passw0rd!xyz"}, headers=H).status_code == 200)
        check("D after deadline: reads still work", un.get("/api/community/my-posts").status_code == 200)
        check("D after deadline: /me still works", un.get("/api/community/me").status_code == 200)
        r = un.post("/api/community/password", json={"old": "wrong-old-pw", "new": "Newpass123!"}, headers=H)
        check("D after deadline: change-password not blocked", "绑定手机号" not in r.text, r.text)
        r = send(un, "86", "13800000090", purpose="bind")
        check("D after deadline: can still request bind code", r.status_code == 200, r.text)
        r = un.post("/api/community/phone/bind", json={"country": "86", "phone": "13800000090", "code": last_code("+8613800000090")}, headers=H)
        check("D after deadline: can bind", r.status_code == 200, r.text)
        r = un.post("/api/me/profile", json={"nickname": "新昵称甲", "bio": ""}, headers=H)
        check("D after binding: writes allowed again", r.status_code == 200, r.text)
        check("D already-verified user unaffected by deadline", old.post("/api/me/profile", json={"nickname": "新昵称乙", "bio": ""}, headers=H).status_code == 200)
        os.environ.pop("PHONE_OTP_ENFORCE_FROM")

        # ======================= E. 微信小程序 getPhoneNumber =======================
        w = newcli()
        dbq("INSERT INTO c_users(email,nickname,pw_hash) VALUES (%s,'小程序用户',%s)", ("w%d@example.com" % rnd, C.hash_pw("Passw0rd!xyz")))
        w.post("/api/community/login", json={"account": "w%d@example.com" % rnd, "password": "Passw0rd!xyz"}, headers=H)
        check("E wx-bind needs login", newcli().post("/api/mp/phone/wx-bind", json={"code": "c"}, headers=H).status_code == 401)
        check("E wx-bind needs code", w.post("/api/mp/phone/wx-bind", json={}, headers=H).status_code == 400)
        r = w.post("/api/mp/phone/wx-bind", json={"code": "c"}, headers=H)
        check("E wx-bind without WECHAT_MP_* configured -> 503", r.status_code == 503, r.text)
        os.environ["WECHAT_MP_APPID"] = "wxtestappid"
        os.environ["WECHAT_MP_SECRET"] = "x"
        orig_wx = PO.wx_get_phone
        PO.wx_get_phone = lambda code: {"country": "86", "phone": "13800000099"} if code == "good" else (_ for _ in ()).throw(__import__("fastapi").HTTPException(400, "微信没有返回手机号，请重试或改用短信验证码"))
        r = w.post("/api/mp/phone/wx-bind", json={"code": "bad"}, headers=H)
        check("E wx-bind bad code -> 400", r.status_code == 400, r.text)
        r = w.post("/api/mp/phone/wx-bind", json={"code": "good"}, headers=H)
        check("E wx-bind ok", r.status_code == 200 and r.json()["phone"] == "+86 138****0099", r.text)
        row = dbq("SELECT phone, phone_verified_at IS NOT NULL, phone_verify_method FROM c_users WHERE email=%s", ("w%d@example.com" % rnd,))
        check("E wx-bind stored verified, method=wechat", row == [("+8613800000099", True, "wechat")], row)
        r = w.post("/api/mp/phone/wx-bind", json={"code": "good"}, headers=H)
        check("E wx-bind twice -> 400", r.status_code == 400, r.text)
        w2 = newcli()
        dbq("INSERT INTO c_users(email,nickname,pw_hash) VALUES (%s,'小程序用户2',%s)", ("w2%d@example.com" % rnd, C.hash_pw("Passw0rd!xyz")))
        w2.post("/api/community/login", json={"account": "w2%d@example.com" % rnd, "password": "Passw0rd!xyz"}, headers=H)
        r = w2.post("/api/mp/phone/wx-bind", json={"code": "good"}, headers=H)
        check("E wx-bind duplicate phone -> 409 (one phone one account)", r.status_code == 409, r.text)
        # Bearer（小程序不带 Cookie）
        tok = "bearer-test-token-%d" % rnd
        uid = dbq("SELECT id FROM c_users WHERE email=%s", ("w2%d@example.com" % rnd,))[0][0]
        dbq("INSERT INTO c_sessions(token_hash,user_id,expires_at) VALUES (%s,%s,now()+interval '1 day')", (hashlib.sha256(tok.encode()).hexdigest(), uid))
        PO.wx_get_phone = lambda code: {"country": "82", "phone": "01077770001"}
        nocookie = newcli()
        r = nocookie.post("/api/mp/phone/wx-bind", json={"code": "good"}, headers=dict(H, Authorization="Bearer " + tok))
        check("E wx-bind via Authorization: Bearer (mini-program)", r.status_code == 200 and r.json()["phone"] == "+82 10-****-0001", r.text)
        # 小程序走短信验证码（Bearer）
        dbq("INSERT INTO c_users(email,nickname,pw_hash) VALUES (%s,'小程序用户3',%s)", ("w3%d@example.com" % rnd, C.hash_pw("Passw0rd!xyz")))
        uid3 = dbq("SELECT id FROM c_users WHERE email=%s", ("w3%d@example.com" % rnd,))[0][0]
        tok3 = "bearer-test-token3-%d" % rnd
        dbq("INSERT INTO c_sessions(token_hash,user_id,expires_at) VALUES (%s,%s,now()+interval '1 day')", (hashlib.sha256(tok3.encode()).hexdigest(), uid3))
        hb = dict(H, Authorization="Bearer " + tok3)
        mp = newcli()
        r = mp.post("/api/community/phone/send", json={"country": "82", "phone": "01099990001", "purpose": "bind"}, headers=hb)
        check("E mini-program sms send via Bearer", r.status_code == 200, r.text)
        r = mp.post("/api/community/phone/bind", json={"country": "82", "phone": "01099990001", "code": last_code("+821099990001")}, headers=hb)
        check("E mini-program sms bind via Bearer", r.status_code == 200 and r.json()["phone"] == "+82 10-****-0001", r.text)
        r = newcli().post("/api/community/phone/bind", json={"country": "82", "phone": "01099990002", "code": "123456"}, headers=dict(H, Authorization="Bearer nope"))
        check("E bad Bearer token -> 401", r.status_code == 401, r.text)
        PO.wx_get_phone = orig_wx
        os.environ.pop("WECHAT_MP_APPID"); os.environ.pop("WECHAT_MP_SECRET")

        # ======================= F. 注销账号 =======================
        row = dbq("SELECT id FROM c_users WHERE phone='+8613800000010'")
        r = g.post("/api/community/account/delete", json={"confirm": "注销", "password": "Passw0rd!xyz"}, headers=H)
        check("F delete account ok", r.status_code == 200, r.text)
        check("F phone freed after deletion", dbq("SELECT count(*) FROM c_users WHERE phone='+8613800000010'")[0][0] == 0)
        check("F OTP rows of deleted user removed", dbq("SELECT count(*) FROM c_phone_otp WHERE user_id=%s", (row[0][0],))[0][0] == 0)

        # ======================= G. 数据库约束兜底 =======================
        import psycopg
        try:
            dbq("INSERT INTO c_users(phone,nickname,pw_hash) VALUES ('+8613800000070','dup','x')")
            check("G DB UNIQUE(phone) backstop", False)
        except psycopg.errors.UniqueViolation:
            check("G DB UNIQUE(phone) backstop", True)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        C._schema_ready = False
        C._psycopg = None

    print("RESULT", "ok", ok, "fail", fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
