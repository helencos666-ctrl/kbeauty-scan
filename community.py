"""旅韩助手 · 会员社区（第一期）
- 手机号/邮箱 + 密码注册登录（scrypt 哈希，会话令牌只存 SHA-256）
- 图文帖子、评论、点赞、举报
- 会员标记（¥9.9，人工开通）与管理员后台
数据存 Postgres（环境变量 DATABASE_URL），图片以 bytea 存库。
密钥只从环境变量读取，不写入日志或前端。
"""
import datetime, hashlib, hmac, io, os, re, secrets, threading, time
from collections import defaultdict, deque
from contextlib import contextmanager

from fastapi import APIRouter, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageOps

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")

BOARDS = {
    "pit": "踩坑经验",
    "group": "组团同行",
    "visa": "签证经验",
    "study": "留学学校",
    "friends": "交友",
}
LOGIN_ONLY_BOARDS = {"friends"}          # 交友版需登录才能查看
MAX_IMAGES = 6
MAX_IMG_BYTES = 6 * 1024 * 1024          # 单张原图上限（前端已压缩，正常远小于此）
IMG_MAX_SIDE, THUMB_SIDE = 1280, 480
SESSION_DAYS = 30
ADMIN_SESSION_HOURS = 12
COOKIE, ADMIN_COOKIE = "lh_sess", "lh_admin"

router = APIRouter()

# ---------------- 数据库 ----------------
_psycopg = None
_schema_ready = False
_schema_lock = threading.Lock()

def _db_url():
    return os.environ.get("DATABASE_URL", "").strip()

SCHEMA = """
CREATE TABLE IF NOT EXISTS c_users (
  id BIGSERIAL PRIMARY KEY,
  phone TEXT UNIQUE,
  email TEXT UNIQUE,
  nickname TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
  is_member BOOLEAN NOT NULL DEFAULT FALSE,
  member_since TIMESTAMPTZ,
  is_banned BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS c_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id BIGINT REFERENCES c_users(id) ON DELETE CASCADE,
  is_admin BOOLEAN NOT NULL DEFAULT FALSE,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS c_posts (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  board TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  like_count INT NOT NULL DEFAULT 0,
  comment_count INT NOT NULL DEFAULT 0,
  deleted BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_posts_board_idx ON c_posts(board, id DESC) WHERE NOT deleted;
CREATE TABLE IF NOT EXISTS c_images (
  id BIGSERIAL PRIMARY KEY,
  post_id BIGINT NOT NULL REFERENCES c_posts(id) ON DELETE CASCADE,
  pos INT NOT NULL DEFAULT 0,
  data BYTEA NOT NULL,
  thumb BYTEA NOT NULL,
  width INT, height INT
);
CREATE INDEX IF NOT EXISTS c_images_post_idx ON c_images(post_id, pos);
CREATE TABLE IF NOT EXISTS c_comments (
  id BIGSERIAL PRIMARY KEY,
  post_id BIGINT NOT NULL REFERENCES c_posts(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  deleted BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_comments_post_idx ON c_comments(post_id, id);
CREATE TABLE IF NOT EXISTS c_likes (
  post_id BIGINT NOT NULL REFERENCES c_posts(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  PRIMARY KEY (post_id, user_id)
);
CREATE TABLE IF NOT EXISTS c_promoters (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  commission_cents INT NOT NULL DEFAULT 300,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS c_ref_visits (
  id BIGSERIAL PRIMARY KEY,
  code TEXT NOT NULL,
  visitor TEXT NOT NULL,
  day DATE NOT NULL,
  path TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (code, visitor, day)
);
CREATE INDEX IF NOT EXISTS c_ref_visits_code_idx ON c_ref_visits(code, created_at);
CREATE TABLE IF NOT EXISTS c_settlements (
  id BIGSERIAL PRIMARY KEY,
  code TEXT NOT NULL,
  members INT NOT NULL,
  amount_cents INT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS promoter_code TEXT;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS ref_settled_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS c_users_promoter_idx ON c_users(promoter_code);
CREATE TABLE IF NOT EXISTS c_reports (
  id BIGSERIAL PRIMARY KEY,
  target_type TEXT NOT NULL,
  target_id BIGINT NOT NULL,
  user_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL,
  reason TEXT NOT NULL,
  resolved BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

@contextmanager
def db():
    global _psycopg, _schema_ready
    url = _db_url()
    if not url:
        raise HTTPException(503, "社区暂未开放，请稍后再来")
    if _psycopg is None:
        import psycopg
        _psycopg = psycopg
    try:
        conn = _psycopg.connect(url, connect_timeout=8)
    except Exception:
        raise HTTPException(503, "社区服务暂时连接不上，请稍后再试")
    try:
        if not _schema_ready:
            with _schema_lock:
                if not _schema_ready:
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_xact_lock(424242)")
                        cur.execute(SCHEMA)
                    conn.commit()
                    _schema_ready = True
        yield conn
        conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception: pass
        raise
    finally:
        conn.close()

def q(conn, sql, args=(), one=False):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        if cur.description is None:
            return None
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows

# ---------------- 限流 ----------------
_rl_lock = threading.Lock()
_rl = defaultdict(deque)

def limit(key: str, n: int, window: int, msg="操作太频繁，请稍后再试"):
    now = time.time()
    with _rl_lock:
        dq = _rl[key]
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= n:
            raise HTTPException(429, msg)
        dq.append(now)

def ip_of(req: Request):
    h = req.headers
    xff = (h.get("x-forwarded-for") or "").split(",")[0].strip()
    return h.get("true-client-ip") or h.get("cf-connecting-ip") or xff or (req.client.host if req.client else "?")

# ---------------- 密码与会话 ----------------
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + dk.hex()

def check_pw(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False

_DUMMY_HASH = None
def dummy_check(pw):
    """账号不存在时也计算一次哈希，避免靠响应时间判断账号是否存在"""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_pw(secrets.token_hex(8))
    check_pw(pw, _DUMMY_HASH)

def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def _secure(req: Request) -> bool:
    if os.environ.get("COOKIE_INSECURE") == "1":
        return False
    proto = (req.headers.get("x-forwarded-proto") or req.url.scheme or "").split(",")[0].strip()
    return proto == "https"

def new_session(conn, resp: Response, req: Request, user_id=None, admin=False):
    token = secrets.token_urlsafe(32)
    if admin:
        exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=ADMIN_SESSION_HOURS)
    else:
        exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=SESSION_DAYS)
    q(conn, "INSERT INTO c_sessions(token_hash,user_id,is_admin,expires_at) VALUES (%s,%s,%s,%s)",
      (_th(token), user_id, admin, exp))
    q(conn, "DELETE FROM c_sessions WHERE expires_at < now()")
    resp.set_cookie(ADMIN_COOKIE if admin else COOKIE, token,
                    max_age=(ADMIN_SESSION_HOURS * 3600 if admin else SESSION_DAYS * 86400),
                    httponly=True, secure=_secure(req), samesite="strict" if admin else "lax", path="/")

def current_user(conn, req: Request):
    tok = req.cookies.get(COOKIE)
    if not tok or len(tok) > 200:
        return None
    return q(conn, """SELECT u.id,u.nickname,u.is_member,u.is_banned,u.phone,u.email,u.created_at
                      FROM c_sessions s JOIN c_users u ON u.id=s.user_id
                      WHERE s.token_hash=%s AND NOT s.is_admin AND s.expires_at>now() AND NOT u.is_banned""",
             (_th(tok),), one=True)

def is_admin(conn, req: Request) -> bool:
    tok = req.cookies.get(ADMIN_COOKIE)
    if not tok or len(tok) > 200:
        return False
    return q(conn, "SELECT 1 AS ok FROM c_sessions WHERE token_hash=%s AND is_admin AND expires_at>now()",
             (_th(tok),), one=True) is not None

def need_user(conn, req):
    u = current_user(conn, req)
    if not u:
        raise HTTPException(401, "请先登录")
    return u

def need_member(conn, req):
    u = need_user(conn, req)
    if not u["is_member"]:
        raise HTTPException(403, "开通会员（¥9.9）后才能发帖和评论")
    return u

def need_admin(conn, req):
    if not is_admin(conn, req):
        raise HTTPException(401, "请先登录管理员后台")

def csrf(req: Request):
    """所有写操作需带自定义请求头；跨站页面无法在不触发预检的情况下伪造"""
    if req.headers.get("x-lh") != "1":
        raise HTTPException(403, "请求无效，请刷新页面重试")

# ---------------- 输入校验 ----------------
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")

def parse_account(raw: str):
    s = (raw or "").strip()
    if "@" in s:
        s = s.lower()
        if not EMAIL_RE.match(s) or len(s) > 254:
            raise HTTPException(400, "邮箱格式不对")
        return "email", s
    digits = re.sub(r"[\s\-()]", "", s)
    if digits.startswith("+"):
        digits = digits[1:]
    if not digits.isdigit():
        raise HTTPException(400, "请输入手机号或邮箱")
    if digits.startswith("0086"): digits = digits[4:]
    if digits.startswith("86") and len(digits) == 13: digits = digits[2:]
    if digits.startswith("82") and len(digits) in (11, 12): digits = "0" + digits[2:]
    if re.fullmatch(r"1[3-9]\d{9}", digits):
        return "phone", "+86" + digits
    if re.fullmatch(r"01[016789]\d{7,8}", digits):
        return "phone", "+82" + digits[1:]
    raise HTTPException(400, "手机号格式不对（支持中国 11 位或韩国 010 开头）")

def clean_text(s, maxlen, field, minlen=1):
    s = (s or "").replace("\r\n", "\n").replace("\x00", "").strip()
    s = re.sub(r"\n{4,}", "\n\n\n", s)
    if len(s) < minlen:
        raise HTTPException(400, f"{field}不能为空" if minlen == 1 else f"{field}至少 {minlen} 个字")
    if len(s) > maxlen:
        raise HTTPException(400, f"{field}最多 {maxlen} 个字")
    return s

def mask_account(u):
    if u.get("email"):
        name, dom = u["email"].split("@", 1)
        return (name[:2] + "***@" + dom)
    p = u.get("phone") or ""
    return p[:5] + "****" + p[-3:] if len(p) > 8 else "***"

def _ts(dt):
    return dt.astimezone(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M") if dt else None

def process_image(raw: bytes):
    if len(raw) > MAX_IMG_BYTES:
        raise HTTPException(413, "单张图片太大，请换一张")
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        raise HTTPException(400, "有图片无法识别，请换 JPG/PNG")
    if im.width * im.height > 50_000_000:
        raise HTTPException(400, "图片尺寸过大")
    im = ImageOps.exif_transpose(im)          # 按拍摄方向摆正，并丢弃 EXIF（含定位）
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA"); bg.paste(im, mask=im.split()[-1])
        else:
            bg.paste(im.convert("RGB"))
        im = bg
    big = im.copy(); big.thumbnail((IMG_MAX_SIDE, IMG_MAX_SIDE))
    th = im.copy(); th.thumbnail((THUMB_SIDE, THUMB_SIDE))
    b1, b2 = io.BytesIO(), io.BytesIO()
    big.convert("RGB").save(b1, "JPEG", quality=78, optimize=True, progressive=True)
    th.convert("RGB").save(b2, "JPEG", quality=70, optimize=True)
    return b1.getvalue(), b2.getvalue(), big.width, big.height

# ---------------- 页面 ----------------
def _page(name):
    return FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-cache"})

@router.get("/community")
def p_community(): return _page("community.html")

@router.get("/community/new")
def p_new(): return _page("community-new.html")

@router.get("/community/post/{pid}")
def p_post(pid: str): return _page("community-post.html")

@router.get("/login")
def p_login(): return _page("login.html")

@router.get("/member")
def p_member(): return _page("member.html")

@router.get("/admin")
def p_admin(): return _page("admin.html")

# ---------------- 账号 API ----------------
@router.get("/api/community/me")
def api_me(req: Request):
    with db() as conn:
        u = current_user(conn, req)
        if not u:
            return {"user": None, "boards": BOARDS}
        return {"user": {"id": u["id"], "nickname": u["nickname"], "is_member": u["is_member"],
                         "account": mask_account(u), "joined": _ts(u["created_at"])}, "boards": BOARDS}

@router.post("/api/community/register")
async def api_register(req: Request, resp: Response):
    csrf(req)
    limit("reg:" + ip_of(req), 20, 3600, "注册太频繁，请一小时后再试")
    d = await req.json()
    kind, acct = parse_account(d.get("account"))
    pw = d.get("password") or ""
    if len(pw) < 8 or len(pw) > 128:
        raise HTTPException(400, "密码需 8–128 位")
    if pw.isdigit() and len(set(pw)) <= 2:
        raise HTTPException(400, "密码太简单，请换一个")
    nick = clean_text(d.get("nickname"), 16, "昵称", 2)
    if re.search(r"管理员|官方|admin", nick, re.I):
        raise HTTPException(400, "昵称不能包含“管理员/官方”")
    if not d.get("agree"):
        raise HTTPException(400, "请先同意社区规则")
    with db() as conn:
        exists = q(conn, f"SELECT id FROM c_users WHERE {kind}=%s", (acct,), one=True)
        if exists:
            raise HTTPException(409, "这个账号已注册，请直接登录")
        ref = resolve_ref(conn, req, d.get("promoter"))
        row = q(conn, f"INSERT INTO c_users({kind},nickname,pw_hash,last_login,promoter_code) VALUES (%s,%s,%s,now(),%s) RETURNING id",
                (acct, nick, hash_pw(pw), ref), one=True)
        new_session(conn, resp, req, user_id=row["id"])
    return {"ok": True}

@router.post("/api/community/login")
async def api_login(req: Request, resp: Response):
    csrf(req)
    limit("login:" + ip_of(req), 10, 600, "登录尝试太多，请十分钟后再试")
    d = await req.json()
    kind, acct = parse_account(d.get("account"))
    pw = d.get("password") or ""
    limit("login-acct:" + acct, 8, 900, "这个账号尝试次数太多，请 15 分钟后再试")
    with db() as conn:
        u = q(conn, f"SELECT id,pw_hash,is_banned FROM c_users WHERE {kind}=%s", (acct,), one=True)
        if not u:
            dummy_check(pw)
            raise HTTPException(401, "账号或密码不对")
        if not check_pw(pw, u["pw_hash"]):
            raise HTTPException(401, "账号或密码不对")
        if u["is_banned"]:
            raise HTTPException(403, "该账号已被封禁")
        q(conn, "UPDATE c_users SET last_login=now() WHERE id=%s", (u["id"],))
        new_session(conn, resp, req, user_id=u["id"])
    return {"ok": True}

@router.post("/api/community/logout")
def api_logout(req: Request, resp: Response):
    csrf(req)
    tok = req.cookies.get(COOKIE)
    if tok and _db_url():
        with db() as conn:
            q(conn, "DELETE FROM c_sessions WHERE token_hash=%s", (_th(tok),))
    resp.delete_cookie(COOKIE, path="/")
    return {"ok": True}

@router.post("/api/community/password")
async def api_change_pw(req: Request):
    csrf(req)
    d = await req.json()
    with db() as conn:
        u = need_user(conn, req)
        limit("pw:" + str(u["id"]), 5, 3600)
        row = q(conn, "SELECT pw_hash FROM c_users WHERE id=%s", (u["id"],), one=True)
        if not check_pw(d.get("old") or "", row["pw_hash"]):
            raise HTTPException(400, "原密码不对")
        new = d.get("new") or ""
        if len(new) < 8 or len(new) > 128:
            raise HTTPException(400, "新密码需 8–128 位")
        q(conn, "UPDATE c_users SET pw_hash=%s WHERE id=%s", (hash_pw(new), u["id"]))
        q(conn, "DELETE FROM c_sessions WHERE user_id=%s AND token_hash<>%s", (u["id"], _th(req.cookies.get(COOKIE))))
    return {"ok": True}

# ---------------- 帖子 API ----------------
def _post_out(r, full=False):
    o = {"id": r["id"], "board": r["board"], "board_name": BOARDS.get(r["board"], ""), "title": r["title"],
         "author": r["nickname"], "author_member": r["is_member"], "likes": r["like_count"],
         "comments": r["comment_count"], "time": _ts(r["created_at"])}
    if full:
        o["body"] = r["body"]
    else:
        b = r["body"].replace("\n", " ")
        o["excerpt"] = b[:80] + ("…" if len(b) > 80 else "")
    return o

@router.get("/api/community/posts")
def api_posts(req: Request, board: str = "", before: int = 0, limit_n: int = 20):
    limit_n = max(1, min(limit_n, 30))
    if board and board not in BOARDS:
        raise HTTPException(400, "版块不存在")
    with db() as conn:
        u = current_user(conn, req)
        if board in LOGIN_ONLY_BOARDS and not u:
            return {"posts": [], "login_required": True}
        cond, args = ["NOT p.deleted", "NOT au.is_banned"], []
        if board:
            cond.append("p.board=%s"); args.append(board)
        elif not u:
            cond.append("p.board <> ALL(%s)"); args.append(list(LOGIN_ONLY_BOARDS))
        if before > 0:
            cond.append("p.id<%s"); args.append(before)
        rows = q(conn, f"""SELECT p.*, au.nickname, au.is_member,
                 (SELECT id FROM c_images i WHERE i.post_id=p.id ORDER BY pos LIMIT 1) AS cover,
                 (SELECT count(*) FROM c_images i WHERE i.post_id=p.id) AS img_n
                 FROM c_posts p JOIN c_users au ON au.id=p.user_id
                 WHERE {' AND '.join(cond)} ORDER BY p.id DESC LIMIT %s""", (*args, limit_n + 1))
    more = len(rows) > limit_n
    out = []
    for r in rows[:limit_n]:
        o = _post_out(r); o["cover"] = r["cover"]; o["img_n"] = r["img_n"]; out.append(o)
    return {"posts": out, "more": more}

@router.get("/api/community/posts/{pid}")
def api_post(req: Request, pid: int):
    with db() as conn:
        u = current_user(conn, req)
        r = q(conn, """SELECT p.*, au.nickname, au.is_member FROM c_posts p JOIN c_users au ON au.id=p.user_id
                       WHERE p.id=%s AND NOT p.deleted AND NOT au.is_banned""", (pid,), one=True)
        if not r:
            raise HTTPException(404, "帖子不存在或已删除")
        if r["board"] in LOGIN_ONLY_BOARDS and not u:
            raise HTTPException(401, "交友版需登录后查看")
        imgs = q(conn, "SELECT id,width,height FROM c_images WHERE post_id=%s ORDER BY pos", (pid,))
        cms = q(conn, """SELECT c.id,c.body,c.created_at,c.user_id,au.nickname,au.is_member FROM c_comments c
                         JOIN c_users au ON au.id=c.user_id
                         WHERE c.post_id=%s AND NOT c.deleted AND NOT au.is_banned ORDER BY c.id LIMIT 500""", (pid,))
        liked = bool(u and q(conn, "SELECT 1 AS x FROM c_likes WHERE post_id=%s AND user_id=%s", (pid, u["id"]), one=True))
    o = _post_out(r, full=True)
    o["images"] = [{"id": i["id"], "w": i["width"], "h": i["height"]} for i in imgs]
    o["liked"] = liked
    o["mine"] = bool(u and u["id"] == r["user_id"])
    o["comment_list"] = [{"id": c["id"], "body": c["body"], "author": c["nickname"], "author_member": c["is_member"],
                          "time": _ts(c["created_at"]), "mine": bool(u and u["id"] == c["user_id"])} for c in cms]
    return o

@router.post("/api/community/posts")
async def api_new_post(req: Request, board: str = Form(...), title: str = Form(...), body: str = Form(...),
                       images: list[UploadFile] = File(default=[])):
    csrf(req)
    if board not in BOARDS:
        raise HTTPException(400, "请选择版块")
    title = clean_text(title, 40, "标题", 4)
    body = clean_text(body, 3000, "正文", 10)
    images = [f for f in (images or []) if f and f.filename]
    if len(images) > MAX_IMAGES:
        raise HTTPException(400, f"最多 {MAX_IMAGES} 张图片")
    processed = []
    for f in images:
        processed.append(process_image(await f.read()))
    with db() as conn:
        u = need_member(conn, req)
        limit("post:" + str(u["id"]), 3, 600, "发帖太频繁，请十分钟后再发")
        n = q(conn, "SELECT count(*) AS n FROM c_posts WHERE user_id=%s AND created_at>now()-interval '1 day'",
              (u["id"],), one=True)["n"]
        if n >= 10:
            raise HTTPException(429, "今天发帖已达上限（10 篇）")
        row = q(conn, "INSERT INTO c_posts(user_id,board,title,body) VALUES (%s,%s,%s,%s) RETURNING id",
                (u["id"], board, title, body), one=True)
        for i, (big, th, w, h) in enumerate(processed):
            q(conn, "INSERT INTO c_images(post_id,pos,data,thumb,width,height) VALUES (%s,%s,%s,%s,%s,%s)",
              (row["id"], i, big, th, w, h))
    return {"ok": True, "id": row["id"]}

@router.delete("/api/community/posts/{pid}")
def api_del_post(req: Request, pid: int):
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        r = q(conn, "UPDATE c_posts SET deleted=TRUE WHERE id=%s AND user_id=%s AND NOT deleted RETURNING id",
              (pid, u["id"]), one=True)
        if not r:
            raise HTTPException(404, "帖子不存在")
    return {"ok": True}

@router.get("/api/community/img/{iid}")
def api_img(req: Request, iid: int, t: int = 0):
    col = "thumb" if t else "data"
    with db() as conn:
        r = q(conn, f"""SELECT i.{col} AS d, p.board FROM c_images i JOIN c_posts p ON p.id=i.post_id
                        JOIN c_users au ON au.id=p.user_id
                        WHERE i.id=%s AND NOT p.deleted AND NOT au.is_banned""", (iid,), one=True)
        if not r:
            raise HTTPException(404, "图片不存在")
        if r["board"] in LOGIN_ONLY_BOARDS and not current_user(conn, req):
            raise HTTPException(401, "请先登录")
    cache = "private, max-age=86400" if r["board"] in LOGIN_ONLY_BOARDS else "public, max-age=604800"
    return Response(bytes(r["d"]), media_type="image/jpeg", headers={"Cache-Control": cache})

@router.post("/api/community/posts/{pid}/comments")
async def api_comment(req: Request, pid: int):
    csrf(req)
    d = await req.json()
    body = clean_text(d.get("body"), 500, "评论")
    with db() as conn:
        u = need_member(conn, req)
        limit("cm:" + str(u["id"]), 10, 300, "评论太频繁，请稍后再试")
        p = q(conn, "SELECT id FROM c_posts WHERE id=%s AND NOT deleted", (pid,), one=True)
        if not p:
            raise HTTPException(404, "帖子不存在或已删除")
        r = q(conn, "INSERT INTO c_comments(post_id,user_id,body) VALUES (%s,%s,%s) RETURNING id", (pid, u["id"], body), one=True)
        q(conn, "UPDATE c_posts SET comment_count=(SELECT count(*) FROM c_comments WHERE post_id=%s AND NOT deleted) WHERE id=%s", (pid, pid))
    return {"ok": True, "id": r["id"]}

@router.delete("/api/community/comments/{cid}")
def api_del_comment(req: Request, cid: int):
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        r = q(conn, "UPDATE c_comments SET deleted=TRUE WHERE id=%s AND user_id=%s AND NOT deleted RETURNING post_id",
              (cid, u["id"]), one=True)
        if not r:
            raise HTTPException(404, "评论不存在")
        _recount(conn, r["post_id"])
    return {"ok": True}

def _recount(conn, pid):
    q(conn, "UPDATE c_posts SET comment_count=(SELECT count(*) FROM c_comments WHERE post_id=%s AND NOT deleted) WHERE id=%s", (pid, pid))

@router.post("/api/community/posts/{pid}/like")
def api_like(req: Request, pid: int):
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        limit("like:" + str(u["id"]), 60, 600)
        if not q(conn, "SELECT id FROM c_posts WHERE id=%s AND NOT deleted", (pid,), one=True):
            raise HTTPException(404, "帖子不存在")
        had = q(conn, "DELETE FROM c_likes WHERE post_id=%s AND user_id=%s RETURNING post_id", (pid, u["id"]), one=True)
        if not had:
            q(conn, "INSERT INTO c_likes(post_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (pid, u["id"]))
        r = q(conn, "UPDATE c_posts SET like_count=(SELECT count(*) FROM c_likes WHERE post_id=%s) WHERE id=%s RETURNING like_count",
              (pid, pid), one=True)
    return {"liked": not had, "likes": r["like_count"]}

@router.post("/api/community/report")
async def api_report(req: Request):
    csrf(req)
    d = await req.json()
    tt = d.get("type")
    if tt not in ("post", "comment"):
        raise HTTPException(400, "举报对象无效")
    try:
        tid = int(d.get("id"))
    except Exception:
        raise HTTPException(400, "举报对象无效")
    reason = clean_text(d.get("reason"), 200, "举报理由")
    with db() as conn:
        u = need_user(conn, req)
        limit("rep:" + str(u["id"]), 10, 3600, "举报太频繁，请稍后再试")
        q(conn, "INSERT INTO c_reports(target_type,target_id,user_id,reason) VALUES (%s,%s,%s,%s)", (tt, tid, u["id"], reason))
    return {"ok": True}

# ---------------- 管理员 API ----------------
@router.post("/api/admin/login")
async def api_admin_login(req: Request, resp: Response):
    csrf(req)
    limit("adm:" + ip_of(req), 5, 900, "尝试太多，请 15 分钟后再试")
    limit("adm-global", 30, 3600, "尝试太多，请稍后再试")
    expected = os.environ.get("ADMIN_PASSWORD", "")
    if len(expected) < 12:
        raise HTTPException(503, "管理员后台未启用（服务器未设置 ADMIN_PASSWORD 或长度不足 12 位）")
    d = await req.json()
    given = (d.get("password") or "").encode()
    if not hmac.compare_digest(hashlib.sha256(given).digest(), hashlib.sha256(expected.encode()).digest()):
        raise HTTPException(401, "密码不对")
    with db() as conn:
        new_session(conn, resp, req, admin=True)
    return {"ok": True}

@router.post("/api/admin/logout")
def api_admin_logout(req: Request, resp: Response):
    csrf(req)
    tok = req.cookies.get(ADMIN_COOKIE)
    if tok and _db_url():
        with db() as conn:
            q(conn, "DELETE FROM c_sessions WHERE token_hash=%s", (_th(tok),))
    resp.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}

@router.get("/api/admin/overview")
def api_admin_overview(req: Request):
    with db() as conn:
        need_admin(conn, req)
        s = q(conn, """SELECT (SELECT count(*) FROM c_users) AS users,
                              (SELECT count(*) FROM c_users WHERE is_member) AS members,
                              (SELECT count(*) FROM c_posts WHERE NOT deleted) AS posts,
                              (SELECT count(*) FROM c_comments WHERE NOT deleted) AS comments,
                              (SELECT count(*) FROM c_reports WHERE NOT resolved) AS reports,
                              pg_database_size(current_database()) AS db_bytes""", one=True)
    s["db_mb"] = round(s.pop("db_bytes") / 1048576, 1)
    return s

@router.get("/api/admin/users")
def api_admin_users(req: Request, s: str = ""):
    with db() as conn:
        need_admin(conn, req)
        s = s.strip()[:60]
        if s:
            like = "%" + s.replace("%", "").replace("_", "") + "%"
            idq = int(s) if s.isdigit() and len(s) < 10 else -1
            rows = q(conn, """SELECT * FROM c_users WHERE id=%s OR nickname ILIKE %s OR email ILIKE %s OR phone ILIKE %s
                              ORDER BY id DESC LIMIT 50""", (idq, like, like, like))
        else:
            rows = q(conn, "SELECT * FROM c_users ORDER BY id DESC LIMIT 50")
    return {"users": [{"id": r["id"], "nickname": r["nickname"], "account": r["email"] or r["phone"],
                       "is_member": r["is_member"], "is_banned": r["is_banned"], "joined": _ts(r["created_at"]),
                       "promoter": r.get("promoter_code"),
                       "member_since": _ts(r["member_since"]), "last_login": _ts(r["last_login"])} for r in rows]}

@router.post("/api/admin/users/{uid}")
async def api_admin_user_action(req: Request, uid: int):
    csrf(req)
    d = await req.json()
    act = d.get("action")
    with db() as conn:
        need_admin(conn, req)
        if not q(conn, "SELECT id FROM c_users WHERE id=%s", (uid,), one=True):
            raise HTTPException(404, "用户不存在")
        if act == "member_on":
            q(conn, "UPDATE c_users SET is_member=TRUE, member_since=COALESCE(member_since, now()) WHERE id=%s", (uid,))
        elif act == "member_off":
            q(conn, "UPDATE c_users SET is_member=FALSE WHERE id=%s", (uid,))
        elif act == "ban":
            q(conn, "UPDATE c_users SET is_banned=TRUE WHERE id=%s", (uid,))
            q(conn, "DELETE FROM c_sessions WHERE user_id=%s", (uid,))
        elif act == "unban":
            q(conn, "UPDATE c_users SET is_banned=FALSE WHERE id=%s", (uid,))
        elif act == "purge":
            q(conn, "UPDATE c_posts SET deleted=TRUE WHERE user_id=%s", (uid,))
            pids = q(conn, "UPDATE c_comments SET deleted=TRUE WHERE user_id=%s RETURNING post_id", (uid,))
            for pid in {p["post_id"] for p in pids}:
                _recount(conn, pid)
        elif act == "reset_pw":
            tmp = secrets.token_urlsafe(8)
            q(conn, "UPDATE c_users SET pw_hash=%s WHERE id=%s", (hash_pw(tmp), uid))
            q(conn, "DELETE FROM c_sessions WHERE user_id=%s", (uid,))
            return {"ok": True, "temp_password": tmp}
        else:
            raise HTTPException(400, "未知操作")
    return {"ok": True}

@router.get("/api/admin/posts")
def api_admin_posts(req: Request, before: int = 0):
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT p.*, au.nickname, au.is_member FROM c_posts p JOIN c_users au ON au.id=p.user_id
                          WHERE NOT p.deleted AND (%s=0 OR p.id<%s) ORDER BY p.id DESC LIMIT 30""", (before, before))
    out = []
    for r in rows:
        o = _post_out(r); o["user_id"] = r["user_id"]; out.append(o)
    return {"posts": out}

@router.delete("/api/admin/posts/{pid}")
def api_admin_del_post(req: Request, pid: int):
    csrf(req)
    with db() as conn:
        need_admin(conn, req)
        q(conn, "UPDATE c_posts SET deleted=TRUE WHERE id=%s", (pid,))
        q(conn, "UPDATE c_reports SET resolved=TRUE WHERE target_type='post' AND target_id=%s", (pid,))
    return {"ok": True}

@router.delete("/api/admin/comments/{cid}")
def api_admin_del_comment(req: Request, cid: int):
    csrf(req)
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "UPDATE c_comments SET deleted=TRUE WHERE id=%s RETURNING post_id", (cid,), one=True)
        if r:
            _recount(conn, r["post_id"])
        q(conn, "UPDATE c_reports SET resolved=TRUE WHERE target_type='comment' AND target_id=%s", (cid,))
    return {"ok": True}

@router.get("/api/admin/reports")
def api_admin_reports(req: Request):
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT r.*, u.nickname AS reporter,
            CASE WHEN r.target_type='post' THEN (SELECT title FROM c_posts WHERE id=r.target_id)
                 ELSE (SELECT body FROM c_comments WHERE id=r.target_id) END AS target_text,
            CASE WHEN r.target_type='post' THEN r.target_id
                 ELSE (SELECT post_id FROM c_comments WHERE id=r.target_id) END AS post_id,
            CASE WHEN r.target_type='post' THEN (SELECT user_id FROM c_posts WHERE id=r.target_id)
                 ELSE (SELECT user_id FROM c_comments WHERE id=r.target_id) END AS target_user
            FROM c_reports r LEFT JOIN c_users u ON u.id=r.user_id
            WHERE NOT r.resolved ORDER BY r.id DESC LIMIT 100""")
    return {"reports": [{"id": r["id"], "type": r["target_type"], "target_id": r["target_id"], "post_id": r["post_id"],
                         "target_user": r["target_user"], "text": (r["target_text"] or "")[:120], "reason": r["reason"],
                         "reporter": r["reporter"], "time": _ts(r["created_at"])} for r in rows]}

@router.post("/api/admin/reports/{rid}/resolve")
def api_admin_resolve(req: Request, rid: int):
    csrf(req)
    with db() as conn:
        need_admin(conn, req)
        q(conn, "UPDATE c_reports SET resolved=TRUE WHERE id=%s", (rid,))
    return {"ok": True}

@router.get("/api/admin/posts/{pid}/comments")
def api_admin_comments(req: Request, pid: int):
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT c.id,c.body,c.created_at,c.user_id,au.nickname FROM c_comments c
                          JOIN c_users au ON au.id=c.user_id WHERE c.post_id=%s AND NOT c.deleted ORDER BY c.id""", (pid,))
    return {"comments": [{"id": r["id"], "body": r["body"], "author": r["nickname"], "user_id": r["user_id"],
                          "time": _ts(r["created_at"])} for r in rows]}

# ---------------- 推广码 ----------------
REF_COOKIE = "lh_ref"
REF_DAYS = 30
CODE_RE = re.compile(r"^[A-Za-z0-9]{2,12}$")
_promo_cache = {"t": 0.0, "codes": set()}

def norm_code(c):
    c = (c or "").strip().upper()
    return c if CODE_RE.match(c) else ""

def active_codes(conn=None):
    if time.time() - _promo_cache["t"] < 60 and conn is None:
        return _promo_cache["codes"]
    def load(c):
        return {r["code"] for r in q(c, "SELECT code FROM c_promoters WHERE active")}
    if conn is not None:
        codes = load(conn)
    else:
        with db() as c:
            codes = load(c)
    _promo_cache.update(t=time.time(), codes=codes)
    return codes

async def ref_middleware(request: Request, call_next):
    """?ref=CODE：首次来源 30 天 Cookie + 记录访问（同一访客同一天只记一次）"""
    code = norm_code(request.query_params.get("ref")) if request.method == "GET" else ""
    if not code or not _db_url() or request.url.path.startswith("/api/"):
        return await call_next(request)
    resp = await call_next(request)
    try:
        await run_in_threadpool(_log_ref, request, resp, code)
    except Exception:
        pass   # 统计失败不影响页面
    return resp

def _log_ref(request, resp, code):
        if code in active_codes():
            visitor = hashlib.sha256((ip_of(request) + "|" + (request.headers.get("user-agent") or "")[:200]).encode()).hexdigest()[:32]
            day = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date()
            with db() as conn:
                q(conn, """INSERT INTO c_ref_visits(code,visitor,day,path) VALUES (%s,%s,%s,%s)
                           ON CONFLICT (code,visitor,day) DO NOTHING""", (code, visitor, day, request.url.path[:100]))
            if not norm_code(request.cookies.get(REF_COOKIE)):
                resp.set_cookie(REF_COOKIE, code, max_age=REF_DAYS * 86400, httponly=True,
                                secure=_secure(request), samesite="lax", path="/")

def resolve_ref(conn, req: Request, manual: str):
    """首次来源优先：Cookie 里有效的推广码优先；Cookie 为空时才用手动填写的"""
    codes = active_codes(conn)
    ck = norm_code(req.cookies.get(REF_COOKIE))
    if ck and ck in codes:
        return ck
    m = (manual or "").strip()
    if not m:
        return None
    mc = norm_code(m)
    if not mc or mc not in codes:
        raise HTTPException(400, "推广码不存在，请核对或留空")
    return mc

@router.get("/api/community/ref")
def api_ref(req: Request):
    ck = norm_code(req.cookies.get(REF_COOKIE))
    return {"ref": ck or None}

def _range(start: str, end: str):
    try:
        s = datetime.date.fromisoformat(start) if start else datetime.date(2000, 1, 1)
        e = datetime.date.fromisoformat(end) if end else datetime.date(2100, 1, 1)
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
    kst = datetime.timezone(datetime.timedelta(hours=9))
    return (datetime.datetime.combine(s, datetime.time(), kst),
            datetime.datetime.combine(e + datetime.timedelta(days=1), datetime.time(), kst))

@router.get("/api/admin/promoters")
def api_admin_promoters(req: Request, start: str = "", end: str = ""):
    t0, t1 = _range(start, end)
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT p.*,
            (SELECT count(*) FROM c_ref_visits v WHERE v.code=p.code AND v.created_at>=%(a)s AND v.created_at<%(b)s) AS visits,
            (SELECT count(*) FROM c_users u WHERE u.promoter_code=p.code AND u.created_at>=%(a)s AND u.created_at<%(b)s) AS regs,
            (SELECT count(*) FROM c_users u WHERE u.promoter_code=p.code AND u.is_member AND u.member_since>=%(a)s AND u.member_since<%(b)s) AS paid,
            (SELECT count(*) FROM c_users u WHERE u.promoter_code=p.code AND u.is_member AND u.ref_settled_at IS NULL) AS unsettled,
            (SELECT COALESCE(sum(amount_cents),0) FROM c_settlements s WHERE s.code=p.code) AS settled_cents
            FROM c_promoters p ORDER BY p.created_at""", {"a": t0, "b": t1})
    return {"promoters": [{"code": r["code"], "name": r["name"], "active": r["active"],
                           "commission": r["commission_cents"] / 100, "visits": r["visits"], "regs": r["regs"],
                           "paid": r["paid"], "commission_in_range": r["paid"] * r["commission_cents"] / 100,
                           "unsettled_members": r["unsettled"], "owed": r["unsettled"] * r["commission_cents"] / 100,
                           "settled_total": r["settled_cents"] / 100} for r in rows]}

def _cents(v):
    try:
        c = round(float(v) * 100)
    except Exception:
        raise HTTPException(400, "佣金金额无效")
    if c < 0 or c > 100000:
        raise HTTPException(400, "佣金金额无效")
    return c

@router.post("/api/admin/promoters")
async def api_admin_new_promoter(req: Request):
    csrf(req)
    d = await req.json()
    code = norm_code(d.get("code"))
    if not code:
        raise HTTPException(400, "推广码只能是 2–12 位字母或数字，例如 A08")
    name = clean_text(d.get("name"), 30, "推广员名称")
    cents = _cents(d.get("commission", 3))
    with db() as conn:
        need_admin(conn, req)
        if q(conn, "SELECT code FROM c_promoters WHERE code=%s", (code,), one=True):
            raise HTTPException(409, "推广码已存在")
        q(conn, "INSERT INTO c_promoters(code,name,commission_cents) VALUES (%s,%s,%s)", (code, name, cents))
        active_codes(conn)
    return {"ok": True, "code": code}

@router.post("/api/admin/promoters/{code}")
async def api_admin_promoter_action(req: Request, code: str):
    csrf(req)
    d = await req.json()
    code = norm_code(code)
    act = d.get("action")
    with db() as conn:
        need_admin(conn, req)
        p = q(conn, "SELECT * FROM c_promoters WHERE code=%s", (code,), one=True)
        if not p:
            raise HTTPException(404, "推广员不存在")
        if act == "commission":
            q(conn, "UPDATE c_promoters SET commission_cents=%s WHERE code=%s", (_cents(d.get("commission")), code))
        elif act in ("disable", "enable"):
            q(conn, "UPDATE c_promoters SET active=%s WHERE code=%s", (act == "enable", code))
        elif act == "settle":
            ids = q(conn, """UPDATE c_users SET ref_settled_at=now() WHERE promoter_code=%s AND is_member
                             AND ref_settled_at IS NULL RETURNING id""", (code,))
            n = len(ids)
            amt = n * p["commission_cents"]
            if n:
                q(conn, "INSERT INTO c_settlements(code,members,amount_cents) VALUES (%s,%s,%s)", (code, n, amt))
            active_codes(conn)
            return {"ok": True, "members": n, "amount": amt / 100}
        else:
            raise HTTPException(400, "未知操作")
        active_codes(conn)
    return {"ok": True}

@router.get("/api/admin/settlements")
def api_admin_settlements(req: Request):
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, "SELECT * FROM c_settlements ORDER BY id DESC LIMIT 100")
    return {"settlements": [{"code": r["code"], "members": r["members"], "amount": r["amount_cents"] / 100,
                             "time": _ts(r["created_at"])} for r in rows]}
