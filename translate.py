"""中韩翻译：Cloudflare Workers AI 免费档。

主模型 mistral-small（여행 장면용 해요체），超时或失败时回退 m2m100。
纠错与翻译在一次 LLM 调用里完成；回译可后台预热缓存。
不打印密钥、账号或完整用户原文。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict

import httpx

log = logging.getLogger("translate")

HANGUL = re.compile(r"[\uac00-\ud7a3]")
HANZI = re.compile(r"[\u4e00-\u9fff]")
CF_API = "https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}"
PRIMARY_MODEL = os.getenv("CF_TRANSLATE_MODEL", "@cf/mistralai/mistral-small-3.1-24b-instruct")
M2M_MODEL = "@cf/meta/m2m100-1.2b"
LLM_TIMEOUT = float(os.getenv("CF_TRANSLATE_TIMEOUT", "2.0"))
M2M_TIMEOUT = float(os.getenv("CF_M2M_TIMEOUT", "1.2"))
MAX_TOKENS = 200

SCENES_ZH = {
    "general": "日常",
    "shop": "购物、免税店、退税",
    "food": "餐厅点餐、结账",
    "taxi": "出租车",
    "pharmacy": "药店买药、说明症状",
    "hotel": "酒店入住、退房",
    "road": "问路",
    "subway": "地铁、换乘、交通卡",
}

# 中国游客常用地名：中文 -> 韩国通行韩文（按长度降序替换）
PLACES = {
    "仁川国际机场": "인천국제공항", "仁川机场": "인천공항", "金浦机场": "김포공항", "金海机场": "김해공항",
    "济州机场": "제주공항", "济州国际机场": "제주국제공항",
    "东大门历史文化公园": "동대문역사문화공원", "东大门设计广场": "동대문디자인플라자",
    "全州韩屋村": "전주한옥마을", "甘川文化村": "감천문화마을",
    "城山日出峰": "성산일출봉", "乐天世界": "롯데월드", "乐天塔": "롯데타워",
    "南山首尔塔": "남산서울타워", "首尔塔": "서울타워", "南山塔": "남산타워",
    "新罗免税店": "신라면세점", "乐天免税店": "롯데면세점", "乐天百货": "롯데백화점",
    "新世界百货": "신세계백화점", "现代百货": "현대백화점",
    "广藏市场": "광장시장", "南大门市场": "남대문시장", "东大门市场": "동대문시장",
    "弘大入口站": "홍대입구역", "弘大入口": "홍대입구", "弘益大学": "홍익대학교",
    "江南站": "강남역", "首尔站": "서울역", "龙山站": "용산역", "明洞站": "명동역",
    "梨泰院站": "이태원역", "新论岘": "신논현", "高速巴士客运站": "고속터미널",
    "建大": "건대", "梨大": "이대", "大学路": "대학로",
    "海云台": "해운대", "广安里": "광안리", "札嘎其": "자갈치", "南浦洞": "남포동",
    "西归浦": "서귀포", "汉拿山": "한라산", "南怡岛": "남이섬", "雪岳山": "설악산",
    "爱宝乐园": "에버랜드", "星空庭院": "코엑스",
    "林荫路": "가로수길", "林荫道": "가로수길",
    "弘大": "홍대", "明洞": "명동", "东大门": "동대문", "南大门": "남대문",
    "江南": "강남", "新村": "신촌", "梨泰院": "이태원", "圣水洞": "성수동", "圣水": "성수",
    "仁寺洞": "인사동", "北村": "북촌", "三清洞": "삼청동", "西村": "서촌",
    "延南洞": "연남동", "延南": "연남", "合井": "합정", "望远": "망원",
    "狎鸥亭": "압구정", "清潭": "청담", "驿三": "역삼", "论岘": "논현", "新沙": "신사",
    "蚕室": "잠실", "汝矣岛": "여의도", "汉南": "한남", "惠化": "혜화", "安国": "안국",
    "钟路": "종로", "乙支路": "을지로", "市厅": "시청", "光化门": "광화문",
    "景福宫": "경복궁", "昌德宫": "창덕궁", "南山": "남산", "汉江": "한강",
    "釜山站": "부산역", "西面": "서면", "釜山": "부산", "济州": "제주",
    "仁川": "인천", "水原": "수원", "大邱": "대구", "全州": "전주", "庆州": "경주",
    "江陵": "강릉", "束草": "속초", "加平": "가평", "春川": "춘천", "坡州": "파주",
    "日山": "일산", "首尔": "서울", "牛岛": "우도",
    "奥利芙杨": "올리브영", "欧利芙杨": "올리브영", "大创": "다이소",
}

# 韩->中回译时用（短词在后，避免截断）
PLACES_KO = {v: k for k, v in PLACES.items()}
# 补充只有韩文侧需要的常用站名
PLACES_KO.update({
    "홍대입구역": "弘大入口站", "강남역": "江南站", "서울역": "首尔站",
    "명동역": "明洞站", "인천공항": "仁川机场", "김포공항": "金浦机场",
    "2호선": "2号线", "1호선": "1号线", "3호선": "3号线", "4호선": "4号线",
    "5호선": "5号线", "6호선": "6号线", "7호선": "7号线", "8호선": "8号线",
    "9호선": "9号线", "분당선": "盆唐线", "신분당선": "新盆唐线",
    "경의중앙선": "京义中央线", "공항철도": "机场铁道",
})

_PLACE_ZH = sorted(PLACES.items(), key=lambda kv: -len(kv[0]))
_PLACE_KO = sorted(PLACES_KO.items(), key=lambda kv: -len(kv[0]))

# 口语：下一站去X = 接下来要去X（不是「다음 역」）
_COLLOQ = (
    (re.compile(r"下一站去"), "接下来要去"),
    (re.compile(r"下一站到(?!了)"), "接下来要到"),
    (re.compile(r"下一站想去"), "接下来想去"),
    (re.compile(r"下一站准备去"), "接下来准备去"),
)

SYS_ZH2KO = """你是在韩国陪中国游客的专业口译。把【中文】译成韩国人日常会说的自然、礼貌해요체韩语。
场景：{scene}
规则：
1. 只把韩文写在 ko 字段，ko 必须是한글，禁止在 ko 里写中文。
2. 忠实原意，不添加、不省略；问句保持问句。句子短、好读，给店员或司机看一眼就懂。
3. 请求用 -아/어 주세요，询问用 -나요?/-아요?/-을 수 있어요?，不要用합니다体或반말。
4. 数字、价格、时间、出口号、航班号照抄。韩元写成 10,000원。
5. 韩国地名用通行韩文：{places}
6. 「下一站去X / 接下来要去X」是行程上的「接下来要去」，译成「이번에 X에 가려고 하는데」这类，绝不要译成「다음 역」。只有原文明确是「下一站是X」才用「다음 역은 X」。
7. 「怎么走 / 怎么去」译成「어떻게 가요?」或「어떻게 가나요?」。
8. 「做成可以退税 / 办理退税 / 办退税」是请店员办理退税，译成「세금 환급 처리해 주세요」或「택스 리펀드 해 주세요」，不要译成「想买可以退税的东西」。
9. 原文用「还是 / 或者」的选择问句，韩语也要保留两个选项。
只输出一行 JSON：{{"ko":"韩语"}}"""

SYS_KO2ZH = """你是在韩国陪中国游客的专业口译。把【韩语】译成自然、口语化的简体中文。
输入可能略有语音识别错字，按最合理的意思理解。
规则：
1. 只把中文写在 zh 字段，必须是简体中文。
2. 数字、价格（韩元写“韩元”或保留 원 前的数字）、时间、出口号准确保留。
3. 地名用常用中文，可在中文后括注韩文，如 弘大（홍대）、江南站（강남역）。
4. 不添加原文没有的内容；问句保持问句。
只输出一行 JSON：{{"zh":"中文"}}"""

SYS_ASR = "输入可能来自语音识别，如有同音错字或缺标点，先按游客在韩国的语境理解真正意思，再翻译。不要把明显的识别错误照译。"

_tcache: OrderedDict = OrderedDict()
_tcache_lock = threading.Lock()
TCACHE_MAX = 3000
CACHE_VER = 2


def cf_creds():
    a = (os.environ.get("CF_ACCOUNT_ID") or "").replace("\ufeff", "").strip()
    t = (os.environ.get("CF_AI_TOKEN") or "").replace("\ufeff", "").strip()
    return a, t


def _tc_get(k):
    with _tcache_lock:
        v = _tcache.get(k)
        if v is not None:
            _tcache.move_to_end(k)
        return v


def _tc_put(k, v):
    with _tcache_lock:
        _tcache[k] = v
        _tcache.move_to_end(k)
        while len(_tcache) > TCACHE_MAX:
            _tcache.popitem(last=False)


def norm_text(t, n=600):
    return re.sub(r"\s+", " ", str(t or "")).strip()[:n]


def pre_normalize_zh(text: str) -> str:
    t = text
    for pat, rep in _COLLOQ:
        t = pat.sub(rep, t)
    return t


def place_hints(text: str, direction: str) -> str:
    if direction == "zh2ko":
        hits = [f"{a}={b}" for a, b in _PLACE_ZH if a in text]
    else:
        hits = [f"{a}={b}" for a, b in _PLACE_KO if a in text]
    if not hits:
        return "弘大=홍대，明洞=명동，东大门=동대문，江南=강남，仁川机场=인천공항"
    # 去重保序，最多 12 个，避免提示过长
    uniq = list(dict.fromkeys(hits))[:12]
    return "、".join(uniq)


def apply_place_ko(text: str, src_zh: str) -> str:
    """若译文里还留着中文地名，换成韩文。"""
    if not text:
        return text
    out = text
    for zh, ko in _PLACE_ZH:
        if zh in src_zh and zh in out:
            out = out.replace(zh, ko)
    return out


# ---------- Revised Romanization（简化连音，够给游客看发音）----------
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = [""] + list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")
_CHO_R = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_JUNG_R = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo",
           "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_JONG_R = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l",
           "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]
_JONG_LIAISON = ["", "g", "kk", "gs", "n", "nj", "nh", "d", "r", "lg", "lm", "lb", "ls", "lt", "lp", "lh",
                 "m", "b", "bs", "s", "ss", "ng", "j", "ch", "k", "t", "p", "h"]


def _syllables(s: str):
    out = []
    for ch in s:
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            x = o - 0xAC00
            cho, jung, jong = x // 588, (x % 588) // 28, x % 28
            out.append(("H", cho, jung, jong, ch))
        else:
            out.append(("O", ch, None, None, ch))
    return out


def romanize(ko: str) -> str:
    """Revised Romanization，词之间保留原文空格与标点。"""
    if not ko:
        return ""
    parts = _syllables(ko)
    buf = []
    i = 0
    n = len(parts)
    while i < n:
        typ, a, b, c, raw = parts[i]
        if typ != "H":
            if raw.isspace():
                if buf and not buf[-1].endswith(" "):
                    buf.append(" ")
            elif raw in ".,!?~…，。？！、·/":
                buf.append(raw)
            elif raw.isascii() and (raw.isalnum() or raw in "-'"):
                if buf and buf[-1].endswith(" "):
                    pass
                elif buf and buf[-1][-1:].isalpha():
                    buf.append(" ")
                buf.append(raw)
            else:
                if buf and not buf[-1].endswith(" "):
                    buf.append(" ")
            i += 1
            continue
        # liaison: 终声 + 下字初声ㅇ
        jong_txt = _JONG_R[c]
        nxt = parts[i + 1] if i + 1 < n else None
        if c and nxt and nxt[0] == "H" and nxt[1] == 11:  # ㅇ
            jong_txt = ""
            # 把终声当作下一音节的初声
            ini = _JONG_LIAISON[c]
            # rewrite next cho virtually by emitting now and skipping silent ㅇ later? 
            # simpler: emit this syllable without batchim, and emit liaison as next initial
            buf.append(_CHO_R[a] + _JUNG_R[b] + ini + _JUNG_R[nxt[2]] + (_JONG_R[nxt[3]] if nxt[3] else ""))
            i += 2
            continue
        buf.append(_CHO_R[a] + _JUNG_R[b] + jong_txt)
        i += 1
    s = "".join(buf)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def extract_llm_text(data: dict) -> str:
    res = (data or {}).get("result") or {}
    out = res.get("response")
    if out is None and res.get("choices"):
        msg = (res["choices"][0] or {}).get("message") or {}
        out = msg.get("content") or msg.get("reasoning") or ""
    if isinstance(out, dict):
        return json.dumps(out, ensure_ascii=False)
    return (out or "").strip() if isinstance(out, str) else ""


def parse_obj(raw: str) -> dict:
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    # 有时模型在前后加说明
    m = re.search(r"\{[^{}]*\}", t, re.S)
    if m:
        t = m.group(0)
    try:
        obj = json.loads(t)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _pick_ko(obj: dict, raw: str) -> str:
    ko = str(obj.get("ko") or "").strip()
    roman_field = str(obj.get("roman") or "").strip()
    # 字段写反：ko 是中文、roman 才是韩文
    if ko and not HANGUL.search(ko) and HANGUL.search(roman_field):
        ko = roman_field
    if not ko and HANGUL.search(raw or ""):
        # 模型没包 JSON，整段就是韩文
        cand = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I)
        if HANGUL.search(cand) and not cand.lstrip().startswith("{"):
            ko = cand.split("\n")[0].strip().strip("\"'")
    return ko


def _pick_zh(obj: dict, raw: str) -> str:
    zh = str(obj.get("zh") or obj.get("ko") or "").strip()
    if zh and not HANZI.search(zh) and HANZI.search(str(obj.get("roman") or "")):
        zh = str(obj.get("roman")).strip()
    if not zh and HANZI.search(raw or ""):
        cand = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I)
        if HANZI.search(cand) and not cand.lstrip().startswith("{"):
            zh = cand.split("\n")[0].strip().strip("\"'")
    return zh


async def _cf_post(account: str, token: str, model: str, body: dict, timeout: float) -> dict:
    url = CF_API.format(acc=account, model=model)
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=body, headers={"Authorization": "Bearer " + token})
    if r.status_code != 200:
        log.warning("cf %s http %s", model.split("/")[-1], r.status_code)
        return {}
    try:
        data = r.json()
    except Exception:
        return {}
    if not data.get("success", True) and not data.get("result"):
        return {}
    return data if isinstance(data, dict) else {}


async def _llm(account, token, messages, timeout):
    body = {"messages": messages, "max_tokens": MAX_TOKENS, "temperature": 0}
    data = await _cf_post(account, token, PRIMARY_MODEL, body, timeout)
    return extract_llm_text(data)


async def _m2m(account, token, text, direction, timeout):
    if direction == "zh2ko":
        body = {"text": text, "source_lang": "zh", "target_lang": "ko"}
    else:
        body = {"text": text, "source_lang": "ko", "target_lang": "zh"}
    data = await _cf_post(account, token, M2M_MODEL, body, timeout)
    res = data.get("result") if data else None
    if isinstance(res, str):
        return res.strip()
    if isinstance(res, dict):
        for k in ("translated_text", "translation", "text", "response"):
            if res.get(k):
                return str(res[k]).strip()
    raw = extract_llm_text(data) if data else ""
    return raw.strip()


def missing_places(src: str, out: str, direction: str):
    """译文漏掉已知地名时返回缺失对照，例如把 홍대 译成「洪水」。"""
    src, out = src or "", out or ""
    hits = []
    if direction == "zh2ko":
        hits = [(zh, ko) for zh, ko in _PLACE_ZH if zh in src]
    else:
        hits = [(ko, zh) for ko, zh in _PLACE_KO if ko in src]
    ok = set()
    for a, b in hits:
        if b in out or a in out:
            ok.add(a)
            ok.add(b)
    miss = []
    for a, b in hits:
        if a in ok or b in ok:
            continue
        if any(len(x) > len(a) and a in x and x in ok for x, _ in hits):
            continue
        miss.append(f"{a}={b}")
    return miss


def _ok_ko(src: str, ko: str) -> bool:
    if not ko or not HANGUL.search(ko):
        return False
    # 译文过短或把解释写进去
    if re.search(r"(我是|作为|翻译助手|sorry|as an ai)", ko, re.I):
        return False
    n_src = max(1, len(re.sub(r"\s+", "", src)))
    n_ko = len(re.sub(r"\s+", "", ko))
    if n_ko > n_src * 6 + 20:
        return False
    return True


def _ok_zh(src: str, zh: str) -> bool:
    if not zh or not HANZI.search(zh):
        return False
    if re.search(r"(我是|作为|翻译助手|sorry|as an ai)", zh, re.I):
        return False
    n_src = max(1, len(re.sub(r"\s+", "", src)))
    n_zh = len(re.sub(r"\s+", "", zh))
    if n_zh > n_src * 6 + 20:
        return False
    return True



def lookup_cached(text: str, direction: str = "zh2ko", scene: str = "general", from_asr: bool = False):
    """命中内存缓存则返回结果副本，否则 None。不发起网络请求。"""
    direction = "ko2zh" if direction == "ko2zh" else "zh2ko"
    scene = scene if scene in SCENES_ZH else "general"
    raw_in = norm_text(text)
    src = pre_normalize_zh(raw_in) if direction == "zh2ko" else raw_in
    if not src:
        return None
    key = (CACHE_VER, direction, scene if direction == "zh2ko" else "", src, bool(from_asr))
    hit = _tc_get(key)
    if not hit:
        return None
    out = dict(hit)
    out.update(cached=True, engine=out.get("engine") or "cache", ms=0, src=src)
    return out


async def translate_text(text: str, direction: str = "zh2ko", scene: str = "general",
                         from_asr: bool = False, want_roman: bool = True) -> dict:
    """返回 {ko,roman} 或 {zh}，另有 engine/ms/src。失败时字段为空字符串。"""
    t0 = time.time()
    direction = "ko2zh" if direction == "ko2zh" else "zh2ko"
    scene = scene if scene in SCENES_ZH else "general"
    raw_in = norm_text(text)
    src = pre_normalize_zh(raw_in) if direction == "zh2ko" else raw_in
    if not src:
        return {"engine": "", "ms": 0, "src": src}

    cache_key = (CACHE_VER, direction, scene if direction == "zh2ko" else "", src, bool(from_asr))
    hit = _tc_get(cache_key)
    if hit:
        out = dict(hit)
        out.update(cached=True, engine=out.get("engine") or "cache", ms=int((time.time() - t0) * 1000), src=src)
        return out

    account, token = cf_creds()
    engine = ""
    result: dict = {}
    rejected_out = ""

    if account and token:
        places = place_hints(src, direction)
        if direction == "zh2ko":
            sys = SYS_ZH2KO.format(scene=SCENES_ZH[scene], places=places)
        else:
            sys = SYS_KO2ZH.format(places=places) if "{places}" in SYS_KO2ZH else SYS_KO2ZH
        if from_asr:
            sys = SYS_ASR + "\n" + sys
        user = src
        if places:
            user = "地名对照：" + places + "\n\n" + src
        try:
            raw = await _llm(account, token, [{"role": "system", "content": sys},
                                              {"role": "user", "content": user}], LLM_TIMEOUT)
            obj = parse_obj(raw)
            if direction == "zh2ko":
                ko = apply_place_ko(_pick_ko(obj, raw), src)
                if _ok_ko(src, ko) and not missing_places(src, ko, "zh2ko"):
                    result = {"ko": ko, "roman": romanize(ko) if want_roman else ""}
                    engine = "llm"
                elif ko:
                    rejected_out = ko
            else:
                zh = _pick_zh(obj, raw)
                if _ok_zh(src, zh) and not missing_places(src, zh, "ko2zh"):
                    result = {"zh": zh}
                    engine = "llm"
                elif zh:
                    rejected_out = zh
        except (httpx.TimeoutException, asyncio.TimeoutError):
            log.warning("llm timeout %s", direction)
        except Exception:
            log.warning("llm failed %s", direction, exc_info=False)

        # 地名漏译（如 홍대→洪水）时再试一次，不把错误结果写入缓存
        if not engine and rejected_out and account and token:
            try:
                hint = "请务必使用这些地名：" + "、".join(place_hints(src, direction).split("、")[:8])
                raw = await _llm(account, token, [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": user + "\n" + hint},
                ], max(0.8, LLM_TIMEOUT - 0.3))
                obj = parse_obj(raw)
                if direction == "zh2ko":
                    ko = apply_place_ko(_pick_ko(obj, raw), src)
                    if _ok_ko(src, ko) and not missing_places(src, ko, "zh2ko"):
                        result = {"ko": ko, "roman": romanize(ko) if want_roman else ""}
                        engine = "llm"
                else:
                    zh = _pick_zh(obj, raw)
                    if _ok_zh(src, zh) and not missing_places(src, zh, "ko2zh"):
                        result = {"zh": zh}
                        engine = "llm"
            except Exception:
                log.warning("llm retry failed %s", direction, exc_info=False)

        if not engine:
            try:
                m2m_in = src
                raw = await _m2m(account, token, m2m_in, direction, M2M_TIMEOUT)
                if direction == "zh2ko":
                    ko = apply_place_ko((raw or "").strip(), src)
                    if _ok_ko(src, ko):
                        result = {"ko": ko, "roman": romanize(ko) if want_roman else ""}
                        engine = "m2m100"
                else:
                    zh = (raw or "").strip()
                    if _ok_zh(src, zh):
                        result = {"zh": zh}
                        engine = "m2m100"
            except Exception:
                log.warning("m2m failed %s", direction, exc_info=False)

    ms = int((time.time() - t0) * 1000)
    if result:
        store = dict(result)
        store["engine"] = engine
        _tc_put(cache_key, store)
        if direction == "zh2ko" and result.get("ko"):
            _schedule_back(result["ko"])
        log.info("translate %s %s %dms", direction, engine, ms)
        out = dict(result)
        out.update(engine=engine, ms=ms, src=src, cached=False)
        return out

    log.warning("translate %s empty %dms", direction, ms)
    empty = {"engine": "", "ms": ms, "src": src, "cached": False}
    if direction == "zh2ko":
        empty.update(ko="", roman="")
    else:
        empty["zh"] = ""
    return empty


_BACK_WARM = True


def _schedule_back(ko: str):
    """回译预热：不阻塞出卡，稍后 /api/back 会打到缓存。"""
    if not _BACK_WARM:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(back_translate(ko))


async def back_translate(ko: str) -> dict:
    t0 = time.time()
    ko = norm_text(ko, 700)
    if not ko or not HANGUL.search(ko):
        return {"zh": "", "ms": 0}
    key = ("back", "", ko, False)
    hit = _tc_get(key)
    if hit:
        out = dict(hit)
        out.update(cached=True, ms=int((time.time() - t0) * 1000))
        return out
    r = await translate_text(ko, "ko2zh", from_asr=False, want_roman=False)
    zh = (r.get("zh") or "").strip()
    out = {"zh": zh, "engine": r.get("engine") or "", "ms": int((time.time() - t0) * 1000), "cached": False}
    if zh:
        _tc_put(key, {"zh": zh, "engine": out["engine"]})
    return out
