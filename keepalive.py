"""Keep the Render free instance awake and run periodic housekeeping.

Why: on the free plan Render spins the service down after ~15 minutes without
inbound traffic; the next visitor (or the WeChat mini program) then waits
30-60 s for a cold start, which users experience as "打不开 / 很卡".  The old
keepalive loop lived on the ops box and stopped on 2026-11-21, after which the
service restarted dozens of times a day (see Render logs).

This thread runs inside the web process: every PING_EVERY seconds it requests
the public /healthz URL (through Render's edge, so it counts as inbound
traffic), and roughly once an hour it re-runs the idempotent official-daily
seed so future-dated posts appear on their date without a redeploy, and warms
the daily-brief caches (weather / exchange rate).

Only enabled on Render (RENDER env var present) or when KEEPALIVE_URL is set;
set KEEPALIVE_DISABLE=1 to turn it off.  Logs never include secrets.
"""
from __future__ import annotations

import os
import random
import threading
import time

PING_EVERY = int(os.environ.get("KEEPALIVE_EVERY", "540"))   # 9 min < 15 min idle limit
HOUSEKEEP_EVERY = 3600
_started = False
_lock = threading.Lock()
STATE = {"started_at": None, "last_ping": None, "last_ping_ok": None, "pings": 0,
         "ping_fail": 0, "last_housekeep": None}


def _url():
    u = os.environ.get("KEEPALIVE_URL", "").strip()
    if u:
        return u
    if os.environ.get("RENDER"):
        base = os.environ.get("RENDER_EXTERNAL_URL", "").strip() or "https://lvhanzhushou.com"
        return base.rstrip("/") + "/healthz?src=keepalive"
    return ""


def _log(msg):
    print("keepalive: " + msg, flush=True)


def _housekeep():
    try:
        from official_daily_seed import seed_once
        seed_once()
    except Exception as e:  # noqa: BLE001
        _log("seed error " + type(e).__name__)
    try:
        import daily_brief
        daily_brief.warm()
    except Exception as e:  # noqa: BLE001
        _log("brief warm error " + type(e).__name__)
    STATE["last_housekeep"] = time.time()


def _loop(url):
    import httpx
    time.sleep(20)
    last_hk = 0.0
    while True:
        t0 = time.time()
        try:
            r = httpx.get(url, timeout=30, headers={"User-Agent": "lvhan-keepalive/1"})
            ok = r.status_code == 200
        except Exception:  # noqa: BLE001
            ok = False
        STATE["pings"] += 1
        STATE["last_ping"] = t0
        STATE["last_ping_ok"] = ok
        if not ok:
            STATE["ping_fail"] += 1
            _log("ping failed")
        if time.time() - last_hk >= HOUSEKEEP_EVERY:
            _housekeep()
            last_hk = time.time()
        time.sleep(max(60, PING_EVERY + random.randint(-30, 30)))


def start():
    global _started
    if os.environ.get("KEEPALIVE_DISABLE") == "1":
        return False
    url = _url()
    if not url:
        return False
    with _lock:
        if _started:
            return True
        _started = True
    STATE["started_at"] = time.time()
    threading.Thread(target=_loop, args=(url,), name="keepalive", daemon=True).start()
    _log("started every %ss" % PING_EVERY)
    return True
