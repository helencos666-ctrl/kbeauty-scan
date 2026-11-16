"""全站每日访问统计：按首尔日期记 PV/UV，只统计 HTML 页面 GET。

UV 用 HMAC(服务端盐, IP|UA|日期) 去重，不保存原始 IP。来源优先记录推广码、
utm 参数和 Referer 域名，便于看出哪条视频或哪个渠道带来访问。
统计写入失败不影响页面响应。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from community import REF_COOKIE, db, ip_of, need_admin, norm_code, q

router = APIRouter()

KST = timezone(timedelta(hours=9))
SALT_KEY = "_visit_salt"
OWN_HOSTS = {
    "lvhanzhushou.com",
    "www.lvhanzhushou.com",
    "kbeauty-scan.onrender.com",
}
SKIP_PATHS = {
    "/healthz",
    "/robots.txt",
    "/favicon.ico",
    "/sw.js",
    "/manifest.webmanifest",
    "/sitemap.xml",
    "/admin",  # 查看后台本身不计入访客
    "/api",
}
SKIP_PREFIXES = ("/api/", "/static/")
# 常见爬虫 / 自动化 UA；大小写不敏感。不匹配普通手机浏览器。
BOT_RE = re.compile(
    r"(bot|crawler|crawl|spider|slurp|scanner|scrapy|ahrefs|semrush|mj12|"
    r"dotbot|bytespider|petalbot|yandex|baiduspider|bingbot|googlebot|"
    r"applebot|facebookexternalhit|facebot|twitterbot|linkedinbot|"
    r"slackbot|telegrambot|whatsapp|pingdom|uptimerobot|"
    r"headlesschrome|phantomjs|curl/|wget/|python-requests|python-urllib|"
    r"go-http-client|aiohttp|httpclient|libwww|okhttp|gptbot|claudebot|"
    r"anthropic|chatgpt-user|ccbot|dataforseo|screaming frog|"
    r"linkpreview|urlpreview|skypeuripreview|render-health|uptime-check)",
    re.I,
)
_salt_cache = {"v": None}

MIGRATE_VISITS = """
CREATE TABLE IF NOT EXISTS c_visit_daily (
  day DATE PRIMARY KEY,
  pv INT NOT NULL DEFAULT 0,
  uv INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS c_visit_uv (
  day DATE NOT NULL,
  visitor_hash TEXT NOT NULL,
  PRIMARY KEY (day, visitor_hash)
);
CREATE TABLE IF NOT EXISTS c_visit_sources (
  day DATE NOT NULL,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  pv INT NOT NULL DEFAULT 0,
  uv INT NOT NULL DEFAULT 0,
  PRIMARY KEY (day, kind, value)
);
CREATE TABLE IF NOT EXISTS c_visit_source_uv (
  day DATE NOT NULL,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  visitor_hash TEXT NOT NULL,
  PRIMARY KEY (day, kind, value, visitor_hash)
);
"""


def today_kst() -> date:
    return datetime.now(KST).date()


def _visit_salt() -> str:
    """服务端盐：优先环境变量 VISIT_SALT，否则在库里生成一次并缓存。不对外返回。"""
    if _salt_cache["v"]:
        return _salt_cache["v"]
    env = (os.environ.get("VISIT_SALT") or "").strip()
    if env:
        _salt_cache["v"] = env
        return env
    with db() as conn:
        row = q(conn, "SELECT value FROM c_settings WHERE key=%s", (SALT_KEY,), one=True)
        if not row:
            val = secrets.token_hex(32)
            q(conn, """INSERT INTO c_settings(key,value) VALUES (%s,%s)
                       ON CONFLICT (key) DO NOTHING""", (SALT_KEY, val))
            row = q(conn, "SELECT value FROM c_settings WHERE key=%s", (SALT_KEY,), one=True)
        _salt_cache["v"] = row["value"]
        return _salt_cache["v"]


def visitor_hash(ip: str, ua: str, day: date) -> str:
    msg = f"{ip}|{(ua or '')[:200]}|{day.isoformat()}".encode()
    return hmac.new(_visit_salt().encode(), msg, hashlib.sha256).hexdigest()[:32]


def is_bot(ua: str) -> bool:
    if not (ua or "").strip():
        return True
    return bool(BOT_RE.search(ua))


def _is_html_page(request: Request, resp) -> bool:
    if request.method != "GET":
        return False
    if resp is not None and getattr(resp, "status_code", 200) >= 400:
        return False
    path = request.url.path or "/"
    if path in SKIP_PATHS or path.startswith(SKIP_PREFIXES):
        return False
    # 带扩展名的基本是资源，不是站点 HTML 路由
    tail = path.rsplit("/", 1)[-1]
    if "." in tail:
        return False
    if resp is not None:
        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype and "text/html" not in ctype:
            return False
    return True


def _clean(s: str, n: int) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())[:n]


def _sources(request: Request) -> list[tuple[str, str]]:
    """按优先级收集来源：推广码、utm、Referer 域名（排除本站）。"""
    out: list[tuple[str, str]] = []
    qp = request.query_params
    code = norm_code(qp.get("ref")) or norm_code(request.cookies.get(REF_COOKIE) or "")
    if code:
        out.append(("ref", code))
    campaign = _clean(qp.get("utm_campaign") or "", 80)
    source = _clean(qp.get("utm_source") or "", 80)
    medium = _clean(qp.get("utm_medium") or "", 40)
    if campaign or source or medium:
        parts = [p for p in (source, medium, campaign) if p]
        out.append(("utm", " / ".join(parts)[:120]))
    ref = request.headers.get("referer") or request.headers.get("referrer") or ""
    try:
        u = urlparse(ref)
        host = (u.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host and host not in OWN_HOSTS and u.scheme in ("http", "https"):
            out.append(("referer", host[:80]))
    except Exception:
        pass
    return out


def _log_visit(request: Request):
    day = today_kst()
    ua = request.headers.get("user-agent") or ""
    ip = ip_of(request)
    vh = visitor_hash(ip, ua, day)
    sources = _sources(request)
    with db() as conn:
        fresh = q(conn, """INSERT INTO c_visit_uv(day, visitor_hash) VALUES (%s,%s)
                           ON CONFLICT DO NOTHING RETURNING visitor_hash""",
                  (day, vh), one=True)
        inc_uv = 1 if fresh else 0
        q(conn, """INSERT INTO c_visit_daily(day,pv,uv) VALUES (%s,1,%s)
                   ON CONFLICT (day) DO UPDATE SET pv=c_visit_daily.pv+1,
                     uv=c_visit_daily.uv+EXCLUDED.uv""", (day, inc_uv))
        for kind, value in sources:
            src_fresh = q(conn, """INSERT INTO c_visit_source_uv(day,kind,value,visitor_hash)
                                   VALUES (%s,%s,%s,%s)
                                   ON CONFLICT DO NOTHING RETURNING visitor_hash""",
                          (day, kind, value, vh), one=True)
            src_uv = 1 if src_fresh else 0
            q(conn, """INSERT INTO c_visit_sources(day,kind,value,pv,uv) VALUES (%s,%s,%s,1,%s)
                       ON CONFLICT (day,kind,value) DO UPDATE SET
                         pv=c_visit_sources.pv+1,
                         uv=c_visit_sources.uv+EXCLUDED.uv""",
              (day, kind, value, src_uv))


async def visit_middleware(request: Request, call_next):
    resp = await call_next(request)
    try:
        ua = request.headers.get("user-agent") or ""
        if is_bot(ua) or not _is_html_page(request, resp):
            return resp
        await run_in_threadpool(_log_visit, request)
    except Exception:
        pass
    return resp


def _day_list(conn, start: date, end: date):
    rows = q(conn, """SELECT day, pv, uv FROM c_visit_daily
                      WHERE day>=%s AND day<=%s ORDER BY day""", (start, end)) or []
    by = {r["day"]: r for r in rows}
    out = []
    d = start
    while d <= end:
        r = by.get(d)
        out.append({"day": d.isoformat(), "pv": int(r["pv"]) if r else 0, "uv": int(r["uv"]) if r else 0})
        d += timedelta(days=1)
    return out


def overview_visits(conn) -> dict:
    today = today_kst()
    start = today - timedelta(days=29)
    days = _day_list(conn, start, today)
    today_row = next((x for x in days if x["day"] == today.isoformat()), {"day": today.isoformat(), "pv": 0, "uv": 0})
    sources = q(conn, """SELECT kind, value, SUM(pv) AS pv, SUM(uv) AS uv
                         FROM c_visit_sources
                         WHERE day>=%s AND day<=%s
                         GROUP BY kind, value
                         ORDER BY SUM(pv) DESC, SUM(uv) DESC
                         LIMIT 15""", (start, today)) or []
    today_sources = q(conn, """SELECT kind, value, pv, uv FROM c_visit_sources
                               WHERE day=%s ORDER BY pv DESC, uv DESC LIMIT 10""", (today,)) or []
    def pack(rows):
        return [{"kind": r["kind"], "value": r["value"], "pv": int(r["pv"]), "uv": int(r["uv"])} for r in rows]
    return {
        "today": today_row,
        "days": days,
        "sources": pack(sources),
        "today_sources": pack(today_sources),
    }


@router.get("/api/admin/visits")
def api_admin_visits(req: Request):
    with db() as conn:
        need_admin(conn, req)
        return overview_visits(conn)
