"""今日旅韩速报：每天自动更新的首页信息卡（无需每天改代码或重新部署）。

内容全部来自可公开引用的数据源，取不到就不显示，不编造：
- 首尔天气：挪威气象局 MET Norway Locationforecast（CC BY 4.0，需注明来源）
- 参考汇率：欧洲央行（ECB）每日参考汇率，经 Frankfurter 公共接口读取
- 近期活动：static/events.json（人工核对的官方活动页面，带来源链接，按日期自动筛选）
- 每日贴士：static/daily-tips.json（取自本站已核对的攻略页，按日期轮换）
- 小编今日帖：数据库里官方账号最近发布的帖子

外部请求只在后台线程里做（keepalive 每小时一次，或缓存过期时触发），接口本身只读缓存，
所以不会因为外部网站慢而拖慢页面。日志不打印密钥或数据库地址。
"""
from __future__ import annotations

import datetime
import json
import os
import threading
import time

from fastapi import APIRouter

router = APIRouter()
KST = datetime.timezone(datetime.timedelta(hours=9))
BASE = os.path.dirname(os.path.abspath(__file__))
UA = "lvhanzhushou.com/1.0 (https://lvhanzhushou.com)"
SEOUL = (37.5665, 126.9780)

_cache = {"weather": None, "weather_t": 0.0, "fx": None, "fx_t": 0.0,
          "official": None, "official_t": 0.0, "visitseoul": None, "visitseoul_t": 0.0}
_lock = threading.Lock()
_refreshing = set()
WEATHER_TTL, FX_TTL, OFFICIAL_TTL, FAIL_RETRY = 3600, 3 * 3600, 600, 600

SYMBOL_ZH = [
    ("thunder", "雷阵雨"), ("heavysnow", "大雪"), ("lightsnow", "小雪"), ("snow", "下雪"),
    ("sleet", "雨夹雪"), ("heavyrain", "大雨"), ("lightrain", "小雨"), ("rain", "下雨"),
    ("fog", "有雾"), ("partlycloudy", "多云"), ("cloudy", "阴"), ("fair", "晴间多云"), ("clearsky", "晴"),
]


def today():
    return datetime.datetime.now(KST).date()


def _sym(code):
    code = (code or "").lower()
    for k, v in SYMBOL_ZH:
        if code.startswith(k):
            return v
    return ""


def _log(msg):
    print("daily brief: " + msg, flush=True)


# ---------------- fetchers (run in background) ----------------
def fetch_weather():
    import httpx
    r = httpx.get("https://api.met.no/weatherapi/locationforecast/2.0/compact",
                  params={"lat": SEOUL[0], "lon": SEOUL[1]}, headers={"User-Agent": UA}, timeout=10)
    r.raise_for_status()
    ts = r.json()["properties"]["timeseries"]
    now_kst = datetime.datetime.now(KST)
    d = now_kst.date()
    label = "今天"
    if now_kst.hour >= 18:          # 傍晚以后展示明天的预报，更有用
        d, label = d + datetime.timedelta(days=1), "明天"
    temps, rain, syms = [], 0.0, []
    now_t = None
    for row in ts:
        t = datetime.datetime.fromisoformat(row["time"].replace("Z", "+00:00")).astimezone(KST)
        det = row["data"]["instant"]["details"]
        if now_t is None:
            now_t = det.get("air_temperature")
        if t.date() != d:
            continue
        if det.get("air_temperature") is not None:
            temps.append(det["air_temperature"])
        n1 = row["data"].get("next_1_hours") or {}
        rain += float((n1.get("details") or {}).get("precipitation_amount") or 0)
        if 8 <= t.hour <= 20:
            s = _sym((n1.get("summary") or {}).get("symbol_code") or
                     ((row["data"].get("next_6_hours") or {}).get("summary") or {}).get("symbol_code"))
            if s:
                syms.append(s)
    if not temps and now_t is None:
        raise ValueError("empty forecast")
    desc = max(set(syms), key=syms.count) if syms else ""
    return {"date": today().isoformat(), "for_date": d.isoformat(), "label": label, "now": round(now_t, 1) if now_t is not None else None,
            "min": round(min(temps), 1) if temps else None, "max": round(max(temps), 1) if temps else None,
            "rain_mm": round(rain, 1), "desc": desc, "city": "首尔",
            "source": "挪威气象局 MET Norway（CC BY 4.0）", "source_url": "https://api.met.no/"}


def fetch_fx():
    import httpx
    r = httpx.get("https://api.frankfurter.dev/v1/latest", params={"base": "CNY", "symbols": "KRW,USD"},
                  headers={"User-Agent": UA}, timeout=10)
    r.raise_for_status()
    j = r.json()
    krw = float(j["rates"]["KRW"])
    if not (100 < krw < 400):
        raise ValueError("rate out of range")
    return {"cny_krw": round(krw, 2), "krw_1000_cny": round(1000 / krw, 2), "date": j.get("date"),
            "source": "欧洲央行参考汇率（经 Frankfurter）", "source_url": "https://www.frankfurter.app/"}


def fetch_official():
    from community import _db_url, db, q
    if not _db_url():
        return []
    with db() as conn:
        rows = q(conn, """SELECT p.id, p.title, p.board, p.created_at FROM c_posts p JOIN c_users u ON u.id=p.user_id
                          WHERE u.is_official AND NOT p.deleted AND COALESCE(p.status,'published')='published'
                          ORDER BY p.id DESC LIMIT 3""")
    return [{"id": r["id"], "title": r["title"], "board": r["board"],
             "date": r["created_at"].astimezone(KST).date().isoformat()} for r in rows]


VS_BASE = "https://chinese.visitseoul.net"


def fetch_visitseoul():
    """首尔市官方旅游网（VisitSeoul，首尔观光财团）的活动日历接口：今天起 4 周内的庆典 + 今天在展的展览。"""
    import httpx
    d = today()
    seen, events, exhibitions = set(), [], []
    with httpx.Client(timeout=10, headers={"User-Agent": UA, "Referer": VS_BASE + "/events-bk"}) as c:
        for off, typ in ((0, "events"), (7, "events"), (14, "events"), (21, "events"), (0, "exhibitions")):
            day = (d + datetime.timedelta(days=off)).strftime("%Y%m%d")
            r = c.post("%s/comm/api/eventListDay/zh-CN/%s/%s" % (VS_BASE, day, typ))
            r.raise_for_status()
            for e in (r.json().get("eventList") or []):
                key = e.get("cid") or e.get("postSj")
                if not key or key in seen or not e.get("postUri", "").startswith("/"):
                    continue
                seen.add(key)
                item = {"id": "vs-" + str(key), "title": (e.get("postSj") or "").strip(),
                        "start": e.get("schdulInfoBgnde"), "end": e.get("schdulInfoEndde"),
                        "place": (e.get("cmmnPlace") or "").strip() or "详见官方页面",
                        "fee": "详见官方页面", "tip": (e.get("sumry") or "").strip()[:80],
                        "source": "首尔市官方旅游网 VisitSeoul", "source_url": VS_BASE + e["postUri"], "auto": True}
                (exhibitions if e["postUri"].startswith("/exhibition/") else events).append(item)
    if not events and not exhibitions:
        raise ValueError("empty")
    return {"events": events, "exhibitions": exhibitions}


FETCHERS = {"weather": (fetch_weather, WEATHER_TTL), "fx": (fetch_fx, FX_TTL), "official": (fetch_official, OFFICIAL_TTL),
            "visitseoul": (fetch_visitseoul, 12 * 3600)}


def _refresh(name):
    fn, _ttl = FETCHERS[name]
    try:
        val = fn()
        with _lock:
            _cache[name], _cache[name + "_t"] = val, time.time()
    except Exception as e:  # noqa: BLE001
        _log("%s fetch failed %s" % (name, type(e).__name__))
        with _lock:   # keep the old value; retry after FAIL_RETRY
            _cache[name + "_t"] = time.time() - FETCHERS[name][1] + FAIL_RETRY
    finally:
        _refreshing.discard(name)


def get(name, background=True):
    """Return the cached value; if stale, refresh in a background thread (non-blocking)."""
    _fn, ttl = FETCHERS[name]
    with _lock:
        val, t = _cache[name], _cache[name + "_t"]
    stale = time.time() - t > ttl
    if name == "weather" and val and val.get("date") != today().isoformat():
        stale, val = True, None          # yesterday's forecast is not "today"
    if stale and name not in _refreshing:
        _refreshing.add(name)
        if background:
            threading.Thread(target=_refresh, args=(name,), daemon=True).start()
        else:
            _refresh(name)
            with _lock:
                val = _cache[name]
    return val


def warm():
    for n in FETCHERS:
        with _lock:
            _cache[n + "_t"] = 0.0
        get(n, background=False)


# ---------------- static data ----------------
def _load(name):
    try:
        with open(os.path.join(BASE, "static", name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _norm(t):
    import re
    return re.sub(r"[\s·・\-—–:：()（）《》<>“”\"']", "", t or "").lower()


def events_for(d, horizon=21, limit=6, extra=None):
    out, seen = [], set()
    for e in list(_load("events.json").get("items", [])) + list(extra or []):
        n = _norm(e.get("title"))
        if not n or any(n in x or x in n for x in seen):
            continue
        seen.add(n)
        try:
            s, t = datetime.date.fromisoformat(e["start"]), datetime.date.fromisoformat(e["end"])
        except Exception:  # noqa: BLE001
            continue
        if t < d or (s - d).days > horizon:
            continue
        item = dict(e)
        item["status"] = "ongoing" if s <= d else "upcoming"
        item["days_to_start"] = max(0, (s - d).days)
        item["days_left"] = (t - d).days
        out.append(item)
    out.sort(key=lambda x: (x["status"] != "ongoing", x["days_to_start"], x["days_left"]))
    return out[:limit]


def tip_for(d):
    tips = _load("daily-tips.json").get("items", [])
    if not tips:
        return None
    return tips[d.toordinal() % len(tips)]


@router.get("/api/daily-brief")
def api_daily_brief():
    d = today()
    wd = "一二三四五六日"[d.weekday()]
    vs = get("visitseoul") or {}
    exh = [x for x in (vs.get("exhibitions") or []) if (x.get("end") or "") >= d.isoformat()]
    if exh:   # 每天换两个在展展览
        k = d.toordinal() % len(exh)
        exh = (exh[k:] + exh[:k])[:2]
    return {"date": d.isoformat(), "weekday": "周" + wd, "weather": get("weather"), "fx": get("fx"),
            "events": events_for(d, extra=vs.get("events")), "exhibitions": exh,
            "more_events_url": VS_BASE + "/events-bk",
            "tip": tip_for(d), "official": get("official") or [],
            "note": "天气、汇率每天自动更新，仅供参考；活动以主办方官方公告为准。"}
