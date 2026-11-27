"""今日旅韩速报 + 健康检查 + 保活：离线单元测试（外部接口全部打桩，不联网、不连数据库）。"""
import datetime, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("DATABASE_URL", None)
os.environ.pop("RENDER", None)
ok = fail = 0
def check(name, cond, info=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name, str(info)[:300])

import daily_brief as B
calls = {"n": 0}
def fake_weather():
    calls["n"] += 1
    return {"date": B.today().isoformat(), "for_date": B.today().isoformat(), "label": "今天", "now": 3.0, "min": -1.0, "max": 5.0,
            "rain_mm": 0.0, "desc": "晴", "city": "首尔", "source": "MET", "source_url": "https://api.met.no/"}
def boom(): raise RuntimeError("offline")
B.FETCHERS["weather"] = (fake_weather, 3600)
B.FETCHERS["fx"] = (lambda: {"cny_krw": 201.5, "krw_1000_cny": 4.96, "date": "2026-11-26", "source": "ECB", "source_url": "x"}, 3600)
B.FETCHERS["official"] = (lambda: [{"id": 9, "title": "小编帖", "board": "daily", "date": B.today().isoformat()}], 600)
B.FETCHERS["visitseoul"] = (lambda: {"events": [{"id": "vs-1", "title": "2026江南之眼媒体冬季嘉年华", "start": "2026-12-03", "end": "2026-12-27", "place": "p", "fee": "f", "source": "VS", "source_url": "u"},
                                                {"id": "vs-2", "title": "测试官方庆典", "start": B.today().isoformat(), "end": B.today().isoformat(), "place": "p", "fee": "f", "source": "VS", "source_url": "u"}],
                                     "exhibitions": [{"id": "vs-3", "title": "展览A", "start": "2026-01-01", "end": "2099-01-01", "place": "馆", "source_url": "u"}]}, 3600)
B.warm()
d = B.api_daily_brief()
check("brief date is today KST", d["date"] == B.today().isoformat())
check("weather from cache", d["weather"] and d["weather"]["min"] == -1.0)
check("fx from cache", d["fx"]["cny_krw"] == 201.5)
check("official posts", d["official"][0]["id"] == 9)
titles = [e["title"] for e in d["events"]]
check("visitseoul duplicate of curated event removed", titles.count("2026江南之眼媒体冬季嘉年华") <= 1, titles)
check("ongoing official event listed first", d["events"] and d["events"][0]["status"] == "ongoing", d["events"][:1])
check("exhibitions present", d["exhibitions"] and d["exhibitions"][0]["title"] == "展览A")
check("tip rotates from file", d["tip"] and d["tip"]["title"])
n0 = calls["n"]; B.api_daily_brief(); B.api_daily_brief()
check("cached: no refetch within TTL", calls["n"] == n0)
# failure keeps old value
B.FETCHERS["weather"] = (boom, 3600)
B._cache["weather_t"] = 0.0
B.get("weather", background=False)
check("fetch failure keeps last good weather", B._cache["weather"] and B._cache["weather"]["min"] == -1.0)
# static data sanity
ev = json.load(open(os.path.join(B.BASE, "static", "events.json"), encoding="utf-8"))
check("events.json items have official source urls", all(e["source_url"].startswith("https://") for e in ev["items"]))
check("events.json dates valid", all(datetime.date.fromisoformat(e["start"]) <= datetime.date.fromisoformat(e["end"]) for e in ev["items"]))
tips = json.load(open(os.path.join(B.BASE, "static", "daily-tips.json"), encoding="utf-8"))["items"]
check("at least 30 tips", len(tips) >= 30)
bad = [t["title"] for t in tips if any(w in t["title"] + t["body"] for w in ("最", "第一", "唯一", "顶级"))]
check("tips avoid absolute words", not bad, bad)
check("events filter: past events excluded", all(e["end"] >= B.today().isoformat() for e in B.events_for(B.today())))
# app endpoints
from fastapi.testclient import TestClient
import app as A
c = TestClient(A.app)
r = c.get("/api/daily-brief"); check("GET /api/daily-brief 200", r.status_code == 200 and r.json()["date"] == B.today().isoformat(), r.text[:200])
r = c.get("/healthz"); check("healthz ok", r.status_code == 200 and r.json()["ok"] is True)
r = c.get("/healthz/deep"); j = r.json()
check("healthz/deep reports checks", "checks" in j and j["checks"]["events.json"]["ok"] and "keepalive" in j, j)
check("healthz/deep has no secrets", "postgres" not in r.text.lower() and "password" not in r.text.lower())
r = c.get("/api/community/rate"); check("rate api has market rate + manual fallback", r.json().get("source") == "manual" and r.json().get("market", {}).get("cny_krw") == 201.5, r.text)
r = c.get("/"); check("homepage has brief card", 'id="briefBlk"' in r.text and "/api/daily-brief" in r.text)
import keepalive
check("keepalive off when not on Render", keepalive.start() is False)
os.environ["RENDER"] = "1"; os.environ["KEEPALIVE_DISABLE"] = "1"
check("keepalive can be disabled", keepalive.start() is False)
os.environ.pop("KEEPALIVE_DISABLE"); os.environ.pop("RENDER")
check("keepalive url on render", (os.environ.__setitem__("RENDER", "1") or keepalive._url()).startswith("https://"))
os.environ.pop("RENDER")
print("%s passed, %s failed" % (ok, fail))
sys.exit(1 if fail else 0)
