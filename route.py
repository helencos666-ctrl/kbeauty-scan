"""任意地点 → 任意地点：Kakao 地点搜索 + Kakao 大众交通路线（免费额度内；结果不落库、不缓存）。"""
import asyncio
import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict

import httpx
from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger("route")
router = APIRouter()
BASE = os.path.dirname(os.path.abspath(__file__))
KAKAO = "https://dapi.kakao.com"
HANGUL = re.compile(r"[\uac00-\ud7a3]")
FOREIGN = re.compile(r"[A-Za-z\u4e00-\u9fff]")


def _key():
    return (os.environ.get("KAKAO_REST_KEY") or "").replace("\ufeff", "").strip()


def _cf():
    a = (os.environ.get("CF_ACCOUNT_ID") or "").replace("\ufeff", "").strip()
    t = (os.environ.get("CF_AI_TOKEN") or "").replace("\ufeff", "").strip()
    return a, t


# ---------- 中文/英文 → 韩文 词典（站名、景点、门店、常见品牌） ----------
DICT = {}


def _add(zh, ko):
    zh = (zh or "").strip()
    ko = (ko or "").strip()
    if zh and ko and len(zh) >= 2:
        DICT.setdefault(zh.lower(), ko)


for _f in ("attractions", "shopping-stores", "food"):
    try:
        for it in json.load(open(os.path.join(BASE, "static", _f + ".json"), encoding="utf-8"))["items"]:
            _add(re.sub(r"[（(].*?[)）]", "", it.get("name_zh", "")), it.get("name_ko"))
            _add(it.get("name_zh"), it.get("name_ko"))
    except Exception as e:  # noqa: BLE001
        log.warning("dict %s: %s", _f, e)
try:
    _sub = json.load(open(os.path.join(BASE, "static", "subway.json"), encoding="utf-8"))
    for s in _sub["st"]:
        _add(s[1] + "站", s[0] + "역")
        _add(s[1], s[0] + "역")
        if s[2]:
            _add(s[2], s[0] + "역")
except Exception as e:  # noqa: BLE001
    log.warning("subway dict: %s", e)
BRANDS = {
    "olive young": "올리브영", "oliveyoung": "올리브영", "奥利弗扬": "올리브영", "乐天免税店": "롯데면세점", "乐天免税": "롯데면세점",
    "新罗免税店": "신라면세점", "新世界免税店": "신세계면세점", "现代免税店": "현대면세점", "乐天百货": "롯데백화점",
    "现代百货": "현대백화점", "新世界百货": "신세계백화점", "乐天世界塔": "롯데월드타워", "乐天世界": "롯데월드", "乐天超市": "롯데마트",
    "易买得": "이마트", "emart": "이마트", "daiso": "다이소", "大创": "다이소", "无印良品": "무인양품", "星巴克": "스타벅스",
    "starbucks": "스타벅스", "麦当劳": "맥도날드", "便利店": "편의점", "皮肤科": "피부과", "整形外科": "성형외과", "整形医院": "성형외과",
    "医院": "병원", "药店": "약국", "酒店": "호텔", "机场": "공항", "仁川机场": "인천공항", "金浦机场": "김포공항", "第一航站楼": "제1여객터미널",
    "第二航站楼": "제2여객터미널", "汉江公园": "한강공원", "南山塔": "N서울타워", "首尔塔": "N서울타워", "广藏市场": "광장시장",
    "东大门设计广场": "동대문디자인플라자", "ddp": "동대문디자인플라자", "coex": "코엑스", "星空图书馆": "별마당도서관",
    "the hyundai": "더현대서울", "gentle monster": "젠틀몬스터", "musinsa": "무신사", "abc-mart": "ABC마트", "abc mart": "ABC마트",
    "汗蒸幕": "찜질방", "烤肉": "고기집", "炸鸡": "치킨", "中餐": "중식당", "警察局": "경찰서", "大使馆": "대사관", "中国大使馆": "주한중국대사관",
}
for _k, _v in BRANDS.items():
    DICT[_k.lower()] = _v
_DKEYS = sorted(DICT, key=len, reverse=True)


def dict_ko(q: str) -> str:
    s = q.strip()
    low = s.lower()
    if low in DICT:
        return DICT[low]
    out = low
    for k in _DKEYS:
        if len(k) >= 2 and k in out:
            out = out.replace(k, " " + DICT[k] + " ")
    return re.sub(r"\s+", " ", out).strip()


_conv = OrderedDict()
_conv_lock = threading.Lock()

# ---------- 大众交通路线内存缓存（同一对起终点 15 分钟内不重复打 Kakao）----------
RPLAN_TTL = 900          # 秒
RPLAN_MAX = 200          # 条数上限
_rplan = OrderedDict()
_rplan_lock = threading.Lock()


def _rplan_key(sx, sy, ex, ey, sn, en):
    return (round(sx, 4), round(sy, 4), round(ex, 4), round(ey, 4), sn, en)


def _rplan_get(k):
    now = time.time()
    with _rplan_lock:
        it = _rplan.get(k)
        if not it:
            return None
        body, ts = it
        if now - ts > RPLAN_TTL:
            _rplan.pop(k, None)
            return None
        _rplan.move_to_end(k)
        return body


def _rplan_put(k, body):
    with _rplan_lock:
        _rplan[k] = (body, time.time())
        _rplan.move_to_end(k)
        while len(_rplan) > RPLAN_MAX:
            _rplan.popitem(last=False)

CONV_PROMPT = ("把中国游客在韩国输入的地点名称转换成适合在 Kakao 地图搜索的韩文关键词。"
               "可能是中文、英文、拼音或韩文罗马字（例如 dayone→데이원，myeongdong→명동），也可能是品牌名+分店。"
               "输出 JSON：{\"queries\":[\"最可能的韩文关键词\",\"备选\"]}，最多 3 个，按可能性排序；"
               "地名+店名用空格分开（如\"올리브영 명동\"），诊所类可给\"피부과\"或\"클리닉\"写法的备选；不要解释。")


async def llm_queries(q: str):
    with _conv_lock:
        if q in _conv:
            _conv.move_to_end(q)
            return _conv[q]
    a, t = _cf()
    res = []
    if a and t:
        try:
            body = {"messages": [{"role": "system", "content": CONV_PROMPT}, {"role": "user", "content": q}],
                    "max_tokens": 120, "temperature": 0}
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.post(f"https://api.cloudflare.com/client/v4/accounts/{a}/ai/run/@cf/mistralai/mistral-small-3.1-24b-instruct",
                                 json=body, headers={"Authorization": "Bearer " + t})
            out = ((r.json().get("result") or {}).get("response") or "") if r.status_code == 200 else ""
            if not isinstance(out, str):
                out = json.dumps(out, ensure_ascii=False)
            m = re.search(r"\{.*\}", out, re.S)
            if m:
                res = [str(x).strip() for x in json.loads(m.group(0)).get("queries", []) if str(x).strip()][:3]
        except Exception as e:  # noqa: BLE001
            log.warning("place llm: %s", type(e).__name__)
    res = [x[:40] for x in res if HANGUL.search(x) or re.search(r"[A-Za-z]", x)]
    if res:
        with _conv_lock:
            _conv[q] = res
            while len(_conv) > 2000:
                _conv.popitem(last=False)
    return res


CAT_ZH = {"MT1": "大型超市", "CS2": "便利店", "PS3": "幼儿园", "SC4": "学校", "AC5": "培训机构", "PK6": "停车场", "OL7": "加油站",
          "SW8": "地铁站", "BK9": "银行", "CT1": "文化设施", "AG2": "中介", "PO3": "公共机关", "AT4": "景点", "AD5": "住宿",
          "FD6": "餐厅", "CE7": "咖啡店", "HP8": "医院", "PM9": "药店"}


async def _kw(c, key, query, x=None, y=None):
    p = {"query": query, "size": 8}
    if x and y:
        p.update(x=x, y=y)
    r = await c.get(KAKAO + "/v2/local/search/keyword.json", params=p, headers={"Authorization": "KakaoAK " + key})
    if r.status_code != 200:
        log.warning("kakao keyword %s", r.status_code)
        return []
    return r.json().get("documents", [])


def _num(v, lo, hi):
    try:
        f = float(v)
    except Exception:  # noqa: BLE001
        return None
    return f if lo <= f <= hi else None


@router.get("/api/place/search")
async def place_search(request: Request, q: str = "", x: str = "", y: str = ""):
    from app import rate_limit
    q = re.sub(r"\s+", " ", q).strip()[:40]
    if len(q) < 1:
        raise HTTPException(400, "请输入地点")
    key = _key()
    if not key:
        raise HTTPException(503, "地点搜索暂未开通")
    rate_limit(request, "place", per_min=30, per_day=400)
    xs, ys = _num(x, 124, 132), _num(y, 33, 39)
    queries = []
    if HANGUL.search(q) and not FOREIGN.search(q):
        queries = [q]
    else:
        d = dict_ko(q)
        if d and not FOREIGN.search(d):
            queries.append(d)
        if not queries or len(q) > 2:
            for s in await llm_queries(q):
                if s not in queries:
                    queries.append(s)
        if not queries:
            queries = [q]
    queries = queries[:3]
    async with httpx.AsyncClient(timeout=5) as c:
        lists = await asyncio.gather(*[_kw(c, key, s, xs, ys) for s in queries], return_exceptions=True)
        if not any(isinstance(x, list) and x for x in lists):
            toks = queries[0].split()
            for v in ([" ".join(toks[:-1]), " ".join(toks[1:])] if len(toks) >= 2 else []):
                lst = await _kw(c, key, v, xs, ys)
                if lst:
                    lists, queries = [lst], queries + [v]
                    break
    seen, out = set(), []
    for lst in lists:
        if isinstance(lst, Exception):
            continue
        for d in lst:
            if d.get("id") in seen:
                continue
            seen.add(d.get("id"))
            out.append({"id": d.get("id"), "name": d.get("place_name"), "addr": d.get("road_address_name") or d.get("address_name"),
                        "cat": CAT_ZH.get(d.get("category_group_code") or "", (d.get("category_name") or "").split(" > ")[-1]),
                        "x": d.get("x"), "y": d.get("y"), "url": d.get("place_url")})
    return {"ok": True, "queries": queries, "places": out[:10], "src": "Kakao"}


# ---------- 路线 ----------
def _hav(a, b):
    R = 6371000
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dla, dlo = la2 - la1, math.radians(b[0] - a[0])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _thin(pts, n=40):
    if len(pts) <= n:
        return [[round(p[0], 5), round(p[1], 5)] for p in pts]
    step = (len(pts) - 1) / (n - 1)
    return [[round(pts[round(i * step)][0], 5), round(pts[round(i * step)][1], 5)] for i in range(n)]


@router.post("/api/route/plan")
async def route_plan(request: Request):
    from app import rate_limit
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        b = {}
    s, e = (b or {}).get("s") or {}, (b or {}).get("e") or {}
    sx, sy, ex, ey = _num(s.get("x"), 124, 132), _num(s.get("y"), 33, 39), _num(e.get("x"), 124, 132), _num(e.get("y"), 33, 39)
    if None in (sx, sy, ex, ey):
        raise HTTPException(400, "起点或终点位置不对，请从列表里选择地点")
    sn, en = str(s.get("name") or "출발")[:40], str(e.get("name") or "도착")[:40]
    dist = _hav((sx, sy), (ex, ey))
    if dist < 60:
        raise HTTPException(400, "起点和终点几乎在同一个地方")
    key = _key()
    if not key:
        raise HTTPException(503, "路线服务暂未开通")
    rate_limit(request, "transit", per_min=6, per_day=40)
    ck = _rplan_key(sx, sy, ex, ey, sn, en)
    t0 = time.time()
    hit = _rplan_get(ck)
    if hit is not None:
        log.info("transit %dms cache", int((time.time() - t0) * 1000))
        return hit
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(KAKAO + "/v2/routing/publictraffic", headers={"Authorization": "KakaoAK " + key},
                            params={"start_x": f"{sx:.6f}", "start_y": f"{sy:.6f}", "end_x": f"{ex:.6f}", "end_y": f"{ey:.6f}",
                                    "s_name": sn, "e_name": en})
    except Exception as ex_:  # noqa: BLE001
        log.warning("transit %dms error %s", int((time.time() - t0) * 1000), type(ex_).__name__)
        raise HTTPException(503, "路线服务暂时连不上")
    if r.status_code != 200:
        log.warning("transit %dms status %s", int((time.time() - t0) * 1000), r.status_code)
        raise HTTPException(503, "今日路线查询次数已满或服务繁忙" if r.status_code == 429 else "路线服务暂时不可用")
    j = r.json()
    walk_only = {"m": int(dist * 1.25), "min": max(1, round(dist * 1.25 / 70))}
    if j.get("status") != "OK":
        body = {"ok": True, "status": j.get("status"), "routes": [], "walk": walk_only, "link": None}
        _rplan_put(ck, body)
        log.info("transit %dms kakao", int((time.time() - t0) * 1000))
        return body
    import datetime as _dt
    hour = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).hour
    day = 5 <= hour < 23
    night_re = re.compile(r"(심야|새벽|^N\d)")
    routes = []
    for rt in (j.get("routes") or [])[:8]:
        p = rt.get("properties") or {}
        steps, ride = [], 0
        for st in rt.get("steps") or []:
            sp = st.get("properties") or {}
            pts = (st.get("path") or {}).get("points") or []
            ride += int(sp.get("time") or 0)
            steps.append({"t": (sp.get("type") or "").lower(), "m": int(sp.get("distance") or 0), "sec": int(sp.get("time") or 0),
                          "g": sp.get("guidance") or "", "stops": [x.get("name") for x in sp.get("stops") or []][:60],
                          "veh": [{"n": v.get("name"), "k": v.get("type")} for v in sp.get("vehicles") or []][:6], "pts": _thin(pts)})
        if day:
            bad = False
            for x in steps:
                if x["t"] == "bus":
                    x["veh"] = [v for v in x["veh"] if not night_re.search(v["n"] or "")]
                    bad = bad or not x["veh"]
            if bad:
                continue
        if not steps:
            continue
        first = steps[0]["pts"][0] if steps[0]["pts"] else [sx, sy]
        last = steps[-1]["pts"][-1] if steps[-1]["pts"] else [ex, ey]
        d1, d2 = _hav((sx, sy), first), _hav(last, (ex, ey))
        total = int(p.get("totalTime") or 0)
        if d1 > 30:
            steps.insert(0, {"t": "walk", "m": int(d1 * 1.25), "sec": int(d1 * 1.25 / 1.15), "g": "", "edge": "start",
                             "stops": [], "veh": [], "pts": [[round(sx, 5), round(sy, 5)], first]})
        if d2 > 30:
            steps.append({"t": "walk", "m": int(d2 * 1.25), "sec": int(d2 * 1.25 / 1.15), "g": "", "edge": "end",
                          "stops": [], "veh": [], "pts": [last, [round(ex, 5), round(ey, 5)]]})
        fare = p.get("fare") or {}
        routes.append({"type": p.get("type"), "min": round(total / 60), "km": round((p.get("totalDistance") or 0) / 1000, 1),
                       "xf": int(p.get("transfers") or 0), "fare": fare.get("value"), "fmin": fare.get("min"), "fmax": fare.get("max"),
                       "walk": round(sum(x["m"] for x in steps if x["t"] in ("walk", "walking"))), "steps": steps})
    routes.sort(key=lambda x: (x["min"] + 4 * x["xf"]))
    link = (j.get("properties") or {}).get("landingURL")
    body = {"ok": True, "status": "OK", "routes": routes[:5], "walk": walk_only, "link": link, "src": "Kakao"}
    _rplan_put(ck, body)
    log.info("transit %dms kakao", int((time.time() - t0) * 1000))
    return body
