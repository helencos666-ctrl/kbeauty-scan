"""旅韩助手 · 安全加固（push17）
- 安全响应头：CSP（脚本用每次请求随机 nonce）、HSTS、nosniff、禁止被嵌入、Referrer-Policy、Permissions-Policy、COOP
- 经代理的 http 请求 301 跳转到 https
- 写操作同源校验（Origin / Referer 必须是本站），配合 X-LH 请求头双重防 CSRF
- 请求体流式限长（没有 Content-Length 的分块上传也会被截断）
- 统一取客户端 IP：只信任 Cloudflare 写入的 CF-Connecting-IP，其次是代理链最右侧地址
- 不读取、不记录任何密钥。
"""
import ipaddress, os, re, secrets
from urllib.parse import urlsplit

from starlette.requests import Request

HSTS = "max-age=31536000; includeSubDomains"
PERMISSIONS = ("camera=(self), microphone=(self), geolocation=(self), payment=(), usb=(), serial=(), "
               "magnetometer=(), gyroscope=(), accelerometer=(), midi=()")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


def csp(nonce: str = "") -> str:
    script = "'self'" + (f" 'nonce-{nonce}'" if nonce else "")
    return ("default-src 'self'; "
            f"script-src {script}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "manifest-src 'self'; "
            "worker-src 'self'; "
            "frame-src 'none'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "upgrade-insecure-requests")


API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

_SCRIPT_TAG = re.compile(r"<script\b(?![^>]*\bnonce=)", re.I)


def new_nonce() -> str:
    return secrets.token_urlsafe(18)


def add_nonce(html_text: str, nonce: str) -> str:
    """给页面里每个 <script> 标签加 nonce；内联脚本因此可以在不开 unsafe-inline 的情况下运行。"""
    return _SCRIPT_TAG.sub(f'<script nonce="{nonce}"', html_text)


# ---------------- 客户端 IP ----------------
def _valid_ip(s: str) -> str:
    s = (s or "").strip()
    if not s or len(s) > 64:
        return ""
    try:
        return str(ipaddress.ip_address(s))
    except ValueError:
        return ""


def client_ip(req: Request) -> str:
    """Render 前面是 Cloudflare：CF-Connecting-IP 由 Cloudflare 覆盖写入，客户端伪造无效。
    X-Forwarded-For 的第一个值可以被客户端伪造，所以只取最右侧（最后一跳代理加上的）地址。"""
    h = req.headers
    ip = _valid_ip(h.get("cf-connecting-ip"))
    if ip:
        return ip
    xff = [p.strip() for p in (h.get("x-forwarded-for") or "").split(",") if p.strip()]
    if xff:
        ip = _valid_ip(xff[-1])
        if ip:
            return ip
    return _valid_ip(req.client.host if req.client else "") or "unknown"


# ---------------- 同源校验 ----------------
def _host_only(netloc: str) -> str:
    return (netloc or "").strip().lower().rstrip(".")


def _allowed_hosts(h: dict) -> set:
    """本站自己的主机名：请求 Host / X-Forwarded-Host、PUBLIC_BASE_URL、正式域名和 Render 默认域名。"""
    out = {_host_only(h.get("host", "")), _host_only(h.get("x-forwarded-host", ""))}
    base = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("SITE_URL") or ""
    if base:
        out.add(_host_only(urlsplit(base).netloc))
    out.update({"lvhanzhushou.com", "www.lvhanzhushou.com", "kbeauty-scan.onrender.com"})
    out.discard("")
    return out


def same_origin(scope_headers: dict) -> bool:
    allowed = _allowed_hosts(scope_headers)
    origin = scope_headers.get("origin")
    if origin is not None:
        if origin == "null":
            return False
        return _host_only(urlsplit(origin).netloc) in allowed
    ref = scope_headers.get("referer")
    if ref:
        return _host_only(urlsplit(ref).netloc) in allowed
    # 没有 Origin/Referer（部分老浏览器、命令行工具）：放行，仍需 X-LH 头 + SameSite Cookie
    return True


# ---------------- ASGI 中间件 ----------------
class SecurityMiddleware:
    def __init__(self, app, max_body: int = 8 * 1024 * 1024 + 100_000, big_paths=None, big_body: int = 20 * 1024 * 1024):
        self.app = app
        self.max_body = max_body
        self.big_paths = set(big_paths or ())
        self.big_body = big_body

    async def _send_simple(self, send, status, body: bytes, ctype=b"application/json; charset=utf-8", extra=()):
        headers = [(b"content-type", ctype), (b"content-length", str(len(body)).encode()),
                   (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                   (b"content-security-policy", API_CSP.encode()), (b"x-frame-options", b"DENY")] + list(extra)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        host = _host_only(headers.get("host", "")).split(":")[0]

        # 1) 经代理的明文 http → https（Render 也会做，这里兜底）
        proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if proto == "http" and host and host not in LOCAL_HOSTS and path != "/healthz":
            qs = scope.get("query_string", b"").decode("latin-1")
            loc = "https://" + headers.get("host", host) + path + (("?" + qs) if qs else "")
            return await self._send_simple(send, 301, b"", b"text/plain", [(b"location", loc.encode("latin-1", "ignore"))])

        # 2) 写操作必须同源
        if method in UNSAFE_METHODS and not same_origin(headers):
            return await self._send_simple(send, 403, '{"detail":"请求无效，请刷新页面重试"}'.encode())

        # 3) 请求体限长（声明的长度 + 实际流式字节数）
        cap = self.big_body if path in self.big_paths else self.max_body
        cl = headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > cap:
            return await self._send_simple(send, 413, '{"detail":"内容太大，请少选几张或换小一点的图"}'.encode())
        received = 0
        too_big = False

        async def limited_receive():
            nonlocal received, too_big
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > cap:
                    too_big = True
                    raise _BodyTooLarge()
            return msg

        started = False

        async def send_wrapper(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                hs = list(message.get("headers", []))
                names = {k.lower() for k, _ in hs}
                ctype = next((v for k, v in hs if k.lower() == b"content-type"), b"").lower()

                def add(k, v):
                    if k not in names:
                        hs.append((k, v)); names.add(k)
                add(b"strict-transport-security", HSTS.encode())
                add(b"x-content-type-options", b"nosniff")
                add(b"x-frame-options", b"DENY")
                add(b"referrer-policy", b"strict-origin-when-cross-origin")
                add(b"permissions-policy", PERMISSIONS.encode())
                add(b"cross-origin-opener-policy", b"same-origin")
                add(b"x-permitted-cross-domain-policies", b"none")
                if b"text/html" in ctype:
                    add(b"content-security-policy", csp().encode())   # 带 nonce 的页面已经自己设置
                else:
                    add(b"content-security-policy", API_CSP.encode() if path.startswith("/api/") else csp().encode())
                if path.startswith("/api/"):
                    add(b"cache-control", b"no-store")
                    add(b"cross-origin-resource-policy", b"same-origin")
                message["headers"] = hs
            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except _BodyTooLarge:
            if not started:
                await self._send_simple(send, 413, '{"detail":"内容太大，请少选几张或换小一点的图"}'.encode())
        except Exception:
            if too_big and not started:
                await self._send_simple(send, 413, '{"detail":"内容太大，请少选几张或换小一点的图"}'.encode())
            else:
                raise


class _BodyTooLarge(Exception):
    pass
