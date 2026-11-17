"""语音识别结果纠错：词库硬替换 + Cloudflare Workers AI 免费额度内的小模型纠错（失败/超时则用原文）。"""
import json
import logging
import os
import re
import time

import httpx

log = logging.getLogger("asr_correct")
_LEX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "asr_lexicon.json"), encoding="utf-8"))
ASR_FIX = sorted(_LEX["asr_fix"].items(), key=lambda kv: -len(kv[0]))
CONF = _LEX["confusions"]
DIALECT = {}
for _region, _m in _LEX["dialect"].items():
    for _k, _v in _m.items():
        if len(_k) >= 2:
            DIALECT.setdefault(_k, _v)
TERMS = _LEX["terms"]
_CAT_KEYS = {
    "medical_aesthetic": "皮肤医美美容整形针管理激光打做脸痘斑皱提拉紧致补水美白疗程麻",
    "medical_general": "药疼痛烧咳医院病急诊过敏",
    "food": "吃喝饭菜辣肉锅汤面酒鸡点单位排队打包",
    "transport": "地铁车站线出口打车出租机场巴士公交高铁",
    "shopping_brands": "买免税退税店百货化妆品牌券折钱卡刷付",
    "hotel_misc": "酒店住房退房前台行李早餐护照换钱",
}
_CORE = "明洞、弘大、东大门、江南、圣水洞、济州、仁川机场、Olive Young、乐天免税店、新罗免税店、医美、皮肤科、肌肤管理、水光针、精华针、丽珠兰、热玛吉、超声刀、T-money"

MODEL = os.getenv("CF_FIX_MODEL", "@cf/mistralai/mistral-small-3.1-24b-instruct")
TIMEOUT = float(os.getenv("ASR_FIX_TIMEOUT", "2.5"))

SYS = ("你是语音识别纠错助手。说话人是在韩国旅游的中国游客，场景是购物、皮肤科/医美、餐厅、打车、地铁、酒店、药店等。"
       "输入是语音识别出来的中文，可能有同音/近音错字、方言口音词、断句和标点缺失。请：\n"
       "1. 只改明显的识别错误，按上下文换成读音相近、意思通顺的词（例如在皮肤科语境“做英明”应为“做医美”）；\n"
       "2. 方言词换成标准普通话（参考提示）；\n"
       "3. 加上合适的标点，去掉重复的口头语（嗯、然后、那个）但保留全部意思；\n"
       "4. 品牌、地名、数字、价格照原样保留，“Day1”这类写法可改成“第一天”；\n"
       "5. 不添加原文没有的信息，不回答问题、不翻译；拿不准就保留原词。\n"
       "只输出纠正后的一句（或几句）中文，不要任何解释或引号。")


def hard_fix(t: str) -> str:
    for a, b in ASR_FIX:
        if a in t:
            t = t.replace(a, b)
    return t


def hints(t: str) -> str:
    out = []
    d = [f"{k}={v}" for k, v in DIALECT.items() if k in t][:25]
    if d:
        out.append("方言词：" + "；".join(d))
    c = [f"{a}→{b}" for a, b in CONF if a in t][:15]
    if c:
        out.append("常见识别错误：" + "；".join(c))
    cats = [k for k, keys in _CAT_KEYS.items() if any(ch in t for ch in keys)]
    words = []
    for k in cats[:3]:
        words += TERMS[k][:60]
    out.append("可能出现的词：" + _CORE + ("、" + "、".join(dict.fromkeys(words)) if words else ""))
    return "\n".join(out)


def _clean(o: str) -> str:
    o = (o or "").strip()
    o = re.sub(r"^(纠正后[的]?[句子文本]*[:：]|输出[:：])\s*", "", o)
    o = o.strip().strip("\"“”「」'")
    return o.split("\n\n")[0].strip()


def _ok(raw: str, out: str) -> bool:
    if not out:
        return False
    if re.search(r"(解释|说明|原文|纠正|抱歉|无法|作为|I )", out) and not re.search(r"(解释|说明|原文|纠正|抱歉|无法|作为)", raw):
        return False
    n_raw, n_out = len(re.sub(r"\W", "", raw)), len(re.sub(r"\W", "", out))
    if n_out < max(1, n_raw * 0.45) or n_out > n_raw * 1.6 + 6:
        return False
    if re.search(r"[\uac00-\ud7a3]", out) and not re.search(r"[\uac00-\ud7a3]", raw):
        return False
    return True


async def correct_zh(raw: str, account: str, token: str, model: str = "", timeout: float = 0):
    """返回 (纠正后文本, 方式)。"""
    raw = (raw or "").strip()
    fixed = hard_fix(raw)
    if not raw or len(raw) < 2 or not (account and token):
        return fixed, "lexicon"
    body = {"messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": hints(fixed) + "\n\n识别原文：" + fixed}],
            "max_tokens": 300, "temperature": 0}
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model or MODEL}"
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as c:
            r = await c.post(url, json=body, headers={"Authorization": "Bearer " + token})
        j = r.json() if r.status_code == 200 else {}
        res = j.get("result") or {}
        out = res.get("response")
        if out is None and res.get("choices"):
            out = res["choices"][0].get("message", {}).get("content")
        out = _clean(out if isinstance(out, str) else "")
        out = hard_fix(out)
        if _ok(fixed, out):
            return out, "llm"
        log.warning("asr fix rejected (%s, %d ms)", r.status_code, int((time.time() - t0) * 1000))
    except Exception as e:  # noqa: BLE001
        log.warning("asr fix failed: %s", type(e).__name__)
    return fixed, "lexicon"
