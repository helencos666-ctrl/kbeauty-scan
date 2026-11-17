"""旅韩助手 · 会员社区（第一期）
- 手机号/邮箱 + 密码注册登录（scrypt 哈希，会话令牌只存 SHA-256）
- 图文帖子、评论、点赞、举报
- 会员标记（¥9.9，人工开通）与管理员后台
数据存 Postgres（环境变量 DATABASE_URL），图片以 bytea 存库。
密钥只从环境变量读取，不写入日志或前端。
"""
import asyncio, datetime, hashlib, hmac, html as html_lib, io, os, re, secrets, threading, time
from collections import defaultdict, deque
from contextlib import contextmanager

from fastapi import APIRouter, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageOps

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")

BOARDS = {   # 键名（pit/group/...）已存库，不要改；只改右边显示名
    "pit": "踩坑避雷",
    "group": "结伴同行",
    "visa": "签证问答",
    "study": "留学打听",
    "friends": "旅韩交友",
    "daily": "在韩日常",
    "hotel": "住宿推荐",
    "car": "包车用车",
    "clinic": "皮肤管理·医美",
    "agency": "留学机构",
    "daigou": "代购商家",
    "guide": "地陪",
    "kpop": "追星活动",
    "logistics": "中韩物流",
    "news": "出行提醒",
    "rent": "租房找房",
    "used": "闲置转让",
    "jobs": "工作机会",
}
# “全部频道”页的分组。可直接改这里调整分组
BOARD_CATEGORIES = [
    {"key": "travel", "name": "旅途路上", "icon": "🧭", "boards": ["pit", "group", "news"]},
    {"key": "visa", "name": "签证·留学", "icon": "🛂", "boards": ["visa", "study"]},
    {"key": "life", "name": "住在韩国", "icon": "🏡", "boards": ["daily", "rent", "used", "jobs"]},
    {"key": "friends", "name": "旅韩交友", "icon": "💗", "boards": ["friends"]},
    {"key": "biz", "name": "商家专区", "icon": "🏷️", "boards": ["hotel", "car", "clinic", "agency", "daigou", "guide", "kpop", "logistics"]},
]
BOARD_ICONS = {"pit": "🚧", "group": "🧳", "news": "📣", "visa": "🛂", "study": "🎓", "rent": "🔑", "used": "♻️",
               "jobs": "💼", "friends": "💗", "hotel": "🛏️", "car": "🚐", "clinic": "✨",
               "agency": "🏫", "daigou": "🛍️", "guide": "🙋", "kpop": "🎤", "logistics": "📦", "daily": "🌸"}
BOARD_DESCS = {"pit": "换钱、打车、购物被坑的真实经历", "group": "找人一起逛、一起拼车、AA 出行", "news": "天气、交通、闭馆等出行变化",
               "visa": "各类签证、K-ETA 的个人经验", "study": "语学院、大学、生活费怎么选", "rent": "看房、合同、押金经验",
               "used": "回国前转让、二手好物", "jobs": "合法打工与求职信息", "friends": "认识新朋友、找搭子、语言交换",
               "hotel": "商家发布的住宿信息", "car": "商家发布的包车、租车信息", "clinic": "合法医疗机构发布的皮肤管理信息",
               "agency": "正规留学机构发布的招生与咨询信息", "daigou": "商家发布的代购服务信息",
               "guide": "商家发布的地陪、向导服务信息", "kpop": "演出、应援、见面会等追星活动信息",
               "logistics": "商家发布的中韩海运、空运、快递、集运信息",
               "daily": "今天在韩国的一件小事，一句话也可以"}
NEWS_BOARDS = ["news", "visa", "study", "pit"]   # “实用干货”标签包含的频道
HOT_HOURS, HOT_FALLBACK_DAYS = 24, 7             # 24 小时热门；24 小时内没有帖子时退回近 7 天
RECOMMEND_DAYS = 30
MERCHANT_BOARDS = {"hotel", "car", "clinic", "agency", "daigou", "guide", "kpop", "logistics"}   # 仅有效商家会员可发
MERCHANT_QUOTA = 10                            # 每 30 天周期 10 篇
# 商家发布信息流分类（可空字段 merchant_cat；all 只用于筛选，不入库）
MERCHANT_CATS = [
    ("car", "租车"),
    ("food", "餐饮美食"),
    ("guide", "地陪向导"),
    ("clinic", "医美"),
    ("logistics", "物流快递"),
    ("daigou", "代购"),
    ("sim", "通讯电话卡"),
    ("other", "其它"),
]
MERCHANT_CAT_KEYS = {k for k, _ in MERCHANT_CATS}
MERCHANT_CAT_LABELS = {k: n for k, n in MERCHANT_CATS}
# 旧帖没有 merchant_cat 时，仅用于筛选的只读兜底，不写回数据库
BOARD_TO_MERCHANT_CAT = {"car": "car", "clinic": "clinic", "guide": "guide", "logistics": "logistics", "daigou": "daigou"}
CYCLE_DAYS = 30
PLAN_DAYS = {"month": 30, "year": 365}
# 普通版块里非商家发布的明显商业信息 → 待审核
COMMERCIAL_WORDS = ["酒店预订", "订酒店", "订房", "民宿预订", "租车", "包车", "接送机", "接机服务", "医美", "皮肤科", "整形", "整容",
                    "微整", "报价", "优惠价", "微信号", "加微", "加v", "vx", "wx:", "wx：", "私信我", "私聊", "代办", "办签",
                    "代购", "地陪", "留学中介", "留学机构", "门票代抢", "代抢票", "联系方式", "电话咨询", "kakao id", "kakaotalk id", "line id"]
PRICE_KEYS = ("price_t1", "price_t2", "price_m_month", "price_m_year", "price_extra", "rate_cny_krw")
BOOL_SETTINGS = {"ai_reply"}
DEFAULT_SETTINGS = {"price_t1": "9.9", "price_t2": "19.9", "price_m_month": "99", "price_m_year": "999", "price_extra": "19.9",
                    "rate_cny_krw": "195", "ai_reply": "1", "chicken_fee_krw": "3000"}
SETTING_LABELS = {"price_t1": "会员价格", "price_t2": "高级会员价格", "price_m_month": "商家会员月付", "price_m_year": "商家会员年付",
                  "price_extra": "商家超额每篇", "rate_cny_krw": "汇率换算：1 人民币 = ? 韩元（手动设置）",
                  "ai_reply": "AI 自动回复（发帖后由旅韩小助手回复）",
                  "chicken_fee_krw": "炸鸡代点服务费（韩元）"}
LOGIN_ONLY_BOARDS = {"friends"}          # 交友版：会员（≥1 档）才能查看
TIER_NAMES = {0: "普通用户", 1: "会员", 2: "高级会员", 3: "商家会员"}
DAILY_BOARD, DAILY_CAP = "daily", 3       # 在韩日常：短标题短正文也可以，每人每天最多 3 篇
# 发帖门槛：会员（¥9.9）即可在日常/实用频道发帖；旅韩交友仍需高级会员；商家专区仍需商家会员
def post_tier(board):
    return 2 if board in LOGIN_ONLY_BOARDS else 1
AUTHOR_COLS = ("au.nickname, COALESCE(au.is_official, FALSE) AS is_official, "
               "(SELECT string_agg(bd.badge, ',' ORDER BY bd.created_at) FROM c_badges bd WHERE bd.user_id=au.id) AS badges")
MAX_IMAGES = 6
MAX_IMG_BYTES = 6 * 1024 * 1024          # 单张原图上限（前端已压缩，正常远小于此）
IMG_MAX_SIDE, THUMB_SIDE = 1280, 480
AVATAR_MAX_BYTES = 5 * 1024 * 1024       # 头像原图上限
AVATAR_SIDE = 256
AVATAR_FMTS = {"JPEG", "PNG", "WEBP", "MPO"}
POST_IMG_FMTS = ("JPEG", "PNG", "WEBP", "GIF", "BMP", "MPO")   # 只启用这些解码器，其它格式一律拒绝
MAX_PIXELS = 50_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS     # 超过直接抛 DecompressionBombError，防“解压炸弹”耗尽内存
SESSION_DAYS = 30
ADMIN_SESSION_HOURS = 12
COOKIE, ADMIN_COOKIE = "lh_sess", "lh_admin"

# 「内容审核」角标与列表共用的 WHERE 条件（含商家待审帖，不含已删帖）
PENDING_POSTS_COND = "p.status='pending' AND NOT p.deleted"
REVIEW_POSTS_COND = "status='pending' AND NOT deleted"
REVIEW_SHOP_RECS_COND = "status='pending'"
REVIEW_FANPLACES_COND = "status IN ('pending','flagged')"

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

# 第二期：会员分档。可在空库和已有库上重复执行（幂等）
MIGRATE = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS tier SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS tier2_since TIMESTAMPTZ;
UPDATE c_users SET tier=1 WHERE is_member AND tier=0;
ALTER TABLE c_promoters ADD COLUMN IF NOT EXISTS commission2_cents INT NOT NULL DEFAULT 600;
CREATE TABLE IF NOT EXISTS c_commissions (
  id BIGSERIAL PRIMARY KEY,
  code TEXT NOT NULL,
  user_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL,
  tier SMALLINT NOT NULL,
  cents INT NOT NULL,
  settled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_commissions_code_idx ON c_commissions(code, created_at);
CREATE INDEX IF NOT EXISTS c_commissions_user_idx ON c_commissions(user_id);
ALTER TABLE c_promoters ADD COLUMN IF NOT EXISTS commission3_cents INT NOT NULL DEFAULT 2000;
ALTER TABLE c_promoters ADD COLUMN IF NOT EXISTS commission4_cents INT NOT NULL DEFAULT 20000;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS merchant_until TIMESTAMPTZ;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS merchant_started TIMESTAMPTZ;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS merchant_plan TEXT;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS merchant_credits INT NOT NULL DEFAULT 0;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'published';
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS is_merchant BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS quota_kind TEXT;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS review_note TEXT;
CREATE INDEX IF NOT EXISTS c_posts_status_idx ON c_posts(status, id DESC);
CREATE TABLE IF NOT EXISTS c_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS c_merchant_log (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES c_users(id) ON DELETE CASCADE,
  action TEXT NOT NULL, detail TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO c_commissions(code,user_id,tier,cents,settled_at,created_at)
  SELECT u.promoter_code, u.id, 1, p.commission_cents, u.ref_settled_at, COALESCE(u.member_since, u.created_at)
  FROM c_users u JOIN c_promoters p ON p.code=u.promoter_code
  WHERE u.tier>=1 AND NOT EXISTS (SELECT 1 FROM c_commissions c WHERE c.user_id=u.id);
"""

# 第三期：社区首页改版（关注、版块订阅）+ 交友/相亲。幂等，可重复执行
MIGRATE3 = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS c_follows (
  follower_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  followee_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (follower_id, followee_id)
);
CREATE INDEX IF NOT EXISTS c_follows_followee_idx ON c_follows(followee_id);
CREATE TABLE IF NOT EXISTS c_board_follows (
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  board TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, board)
);
CREATE INDEX IF NOT EXISTS c_posts_created_idx ON c_posts(created_at DESC) WHERE NOT deleted;
CREATE INDEX IF NOT EXISTS c_posts_user_idx ON c_posts(user_id, id DESC);
CREATE TABLE IF NOT EXISTS c_dating_profiles (
  user_id BIGINT PRIMARY KEY REFERENCES c_users(id) ON DELETE CASCADE,
  nickname TEXT NOT NULL,
  gender TEXT NOT NULL,
  age SMALLINT NOT NULL,
  height SMALLINT,
  city TEXT NOT NULL,
  area TEXT,
  visa TEXT,
  purpose TEXT NOT NULL,
  intro TEXT NOT NULL,
  visible BOOLEAN NOT NULL DEFAULT TRUE,
  hidden_by_admin BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_dating_list_idx ON c_dating_profiles(updated_at DESC) WHERE visible AND NOT hidden_by_admin;
CREATE TABLE IF NOT EXISTS c_dating_photos (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  pos INT NOT NULL DEFAULT 0,
  data BYTEA NOT NULL,
  thumb BYTEA NOT NULL,
  width INT, height INT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_dating_photos_user_idx ON c_dating_photos(user_id, pos, id);
CREATE TABLE IF NOT EXISTS c_threads (
  id BIGSERIAL PRIMARY KEY,
  user_a BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  user_b BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_a, user_b)
);
CREATE INDEX IF NOT EXISTS c_threads_b_idx ON c_threads(user_b);
CREATE TABLE IF NOT EXISTS c_messages (
  id BIGSERIAL PRIMARY KEY,
  thread_id BIGINT NOT NULL REFERENCES c_threads(id) ON DELETE CASCADE,
  sender_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  recipient_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'msg',
  body TEXT NOT NULL,
  flagged BOOLEAN NOT NULL DEFAULT FALSE,
  flag_words TEXT,
  deleted BOOLEAN NOT NULL DEFAULT FALSE,
  read_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_messages_thread_idx ON c_messages(thread_id, id);
CREATE INDEX IF NOT EXISTS c_messages_unread_idx ON c_messages(recipient_id) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS c_messages_sender_idx ON c_messages(sender_id, kind, created_at);
CREATE TABLE IF NOT EXISTS c_blocks (
  blocker_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  blocked_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (blocker_id, blocked_id)
);
"""

# 第四期：系统账号「旅韩小助手」与 AI 自动回复。只追加列，幂等
MIGRATE4 = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS is_bot BOOLEAN NOT NULL DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS c_users_one_bot_idx ON c_users((is_bot)) WHERE is_bot;
"""

# 第五期：对外会员编号 member_no（靓号）。不改主键 id，避免外键牵连
MIGRATE5 = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS member_no BIGINT;
UPDATE c_users SET member_no = id WHERE member_no IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS c_users_member_no_uidx ON c_users(member_no);
"""

# 第六期：用户头像（JPEG 存 bytea，与帖子图片一样放 Postgres，避免 Render 磁盘无持久化）
MIGRATE6 = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS avatar BYTEA;
"""

# 商家发布分类：可空，不回填已有行
MIGRATE7 = """
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS merchant_cat TEXT;
CREATE INDEX IF NOT EXISTS c_posts_merchant_feed_idx
  ON c_posts(id DESC) WHERE is_merchant AND NOT deleted AND status='published';
"""

BOT_NICK = "旅韩小助手"
BOT_PW_HASH = "disabled"          # 非 scrypt 格式，check_pw 恒为失败，不能登录
AI_TIMEOUT = 20
AI_DAILY_LIMIT = 200
AI_USER_HOUR = 6
AI_USER_DAY = 20
AI_MAX_CHARS = 200
DEEPSEEK_API = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
KST = datetime.timezone(datetime.timedelta(hours=9))

AI_SYSTEM = """你是「旅韩小助手」，旅韩助手网站韩友圈里的公开信息助手，不是真人。
规则：
- 用和用户帖子或评论相同的语言回复，默认中文。语气友好、务实、简短，不超过 200 字。
- 只提供公开、可自行核实的参考信息，不充当代办，不打包票。
- 涉及签证、入境、K-ETA 时只说明一般信息，明确不能代办、不能保证结果。
- 涉及医美、皮肤管理、包车、租车、住宿时保持中立，不推荐、不点名具体商家、医院或司机。
- 不留任何联系方式（包括微信、电话、邮箱、二维码、外链）。
- 不提及任何其他社区或网站品牌，尤其不得出现“奋韩”或近似写法。
- 不提及直播、抖音或任何带货内容。
- 可以自称小助手，不要假装是去过当地的真人旅伴。
- 涉及地铁线路、价格、营业时间等具体信息时，提醒用户出发前再核实。
只输出回复正文，不要标题，不要用引号把全文包起来。"""


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
                        cur.execute(MIGRATE)
                        cur.execute(MIGRATE3)
                        cur.execute(MIGRATE4)
                        cur.execute(MIGRATE5)
                        cur.execute(MIGRATE6)
                        cur.execute(MIGRATE7)
                        from points import MIGRATE_POINTS
                        cur.execute(MIGRATE_POINTS)
                        from chicken import MIGRATE_CHICKEN
                        cur.execute(MIGRATE_CHICKEN)
                        from shop_extras import MIGRATE_SHOP_EXTRAS
                        cur.execute(MIGRATE_SHOP_EXTRAS)
                        from shop_extras import MIGRATE_MERCHANT
                        cur.execute(MIGRATE_MERCHANT)
                        from buddy_verify import MIGRATE_BUDDY_VERIFY
                        cur.execute(MIGRATE_BUDDY_VERIFY)
                        from promo import MIGRATE_PROMO
                        cur.execute(MIGRATE_PROMO)
                        from opening_promo import MIGRATE_OPENING_PROMO
                        cur.execute(MIGRATE_OPENING_PROMO)
                        from pay_proof import MIGRATE_PAY_PROOF
                        cur.execute(MIGRATE_PAY_PROOF)
                        from chicken_merchant import MIGRATE_CHICKEN_MERCHANT
                        cur.execute(MIGRATE_CHICKEN_MERCHANT)
                        from contact_guard import MIGRATE_CONTACT_GUARD
                        cur.execute(MIGRATE_CONTACT_GUARD)
                        from merch import MIGRATE_MERCH
                        cur.execute(MIGRATE_MERCH)
                        from retention import MIGRATE_RETENTION
                        cur.execute(MIGRATE_RETENTION)
                        from visits import MIGRATE_VISITS
                        cur.execute(MIGRATE_VISITS)
                        from providers import MIGRATE_PROVIDERS
                        cur.execute(MIGRATE_PROVIDERS)
                        from me import MIGRATE_ME
                        cur.execute(MIGRATE_ME)
                        from chat import MIGRATE_CHAT
                        cur.execute(MIGRATE_CHAT)
                        from fanplaces import MIGRATE_FANPLACES
                        cur.execute(MIGRATE_FANPLACES)
                    ensure_bot(conn)
                    from chicken import ensure_chicken_shops
                    ensure_chicken_shops(conn)
                    from chicken_merchant import hide_example_shops_once
                    hide_example_shops_once(conn)
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
    from security import client_ip     # 只信任 Cloudflare 写入的 IP，防止伪造 X-Forwarded-For 绕过限流
    return client_ip(req)

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
    u = q(conn, """SELECT u.id,u.nickname,u.tier,u.is_banned,u.phone,u.email,u.created_at,
                      u.merchant_until,u.merchant_started,u.merchant_plan,u.merchant_credits,u.verified,
                      u.member_no, u.id_verified, u.buddy_banned_until, u.buddy_ban_reason,
                      (u.avatar IS NOT NULL OR COALESCE(u.is_bot, FALSE)) AS has_avatar,
                      (u.merchant_until IS NOT NULL AND u.merchant_until>now()) AS merchant
                      FROM c_sessions s JOIN c_users u ON u.id=s.user_id
                      WHERE s.token_hash=%s AND NOT s.is_admin AND s.expires_at>now() AND NOT u.is_banned""",
             (_th(tok),), one=True)
    if u:
        u["base_tier"] = u["tier"]
        if u["merchant"]:
            u["tier"] = max(u["tier"], 2)       # 有效商家会员同时拥有高级会员权限
    return u

def cycle_start(u):
    """商家额度周期：从开通日起每 30 天一个周期（年付也每 30 天重置）"""
    st = u["merchant_started"]
    now = datetime.datetime.now(datetime.timezone.utc)
    n = int((now - st).total_seconds() // (CYCLE_DAYS * 86400))
    return st + datetime.timedelta(days=CYCLE_DAYS * n)

def merchant_quota(conn, u):
    if not u.get("merchant"):
        return None
    cs = cycle_start(u)
    used = q(conn, "SELECT count(*) AS n FROM c_posts WHERE user_id=%s AND quota_kind='quota' AND created_at>=%s",
             (u["id"], cs), one=True)["n"]
    return {"used": used, "left": max(0, MERCHANT_QUOTA - used), "credits": u["merchant_credits"],
            "cycle_start": _ts(cs), "cycle_end": _ts(min(cs + datetime.timedelta(days=CYCLE_DAYS), u["merchant_until"])),
            "until": _ts(u["merchant_until"]), "plan": u["merchant_plan"]}

def get_settings(conn):
    out = dict(DEFAULT_SETTINGS)
    for r in q(conn, "SELECT key,value FROM c_settings"):
        if r["key"] in out:
            out[r["key"]] = r["value"]
    return out

def price(conn, key):
    return get_settings(conn)[key]

def is_commercial(text):
    t = text.lower().replace(" ", "")
    hits = [w for w in COMMERCIAL_WORDS if w.replace(" ", "") in t]
    if re.search(r"(微信|weixin|wechat|v信|威信)[:：]?\s*[a-z][-_a-z0-9]{5,19}", text, re.I):
        hits.append("微信号")
    return hits

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

def need_tier(conn, req, tier, msg=None):
    u = need_user(conn, req)
    if u["tier"] < tier:
        st = get_settings(conn)
        m = msg or ("开通会员（¥{t1}）后才能使用" if tier == 1 else "开通高级会员（¥{t2}）后才能发帖")
        raise HTTPException(403, m.format(t1=st["price_t1"], t2=st["price_t2"]))
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

def _open_image(raw: bytes, formats, bad_msg):
    """只用白名单解码器打开；先看尺寸再解码，避免超大像素图片耗尽内存。"""
    try:
        im = Image.open(io.BytesIO(raw), formats=list(formats))
    except Exception:
        raise HTTPException(400, bad_msg)
    if im.width * im.height > MAX_PIXELS or im.width > 20000 or im.height > 20000:
        raise HTTPException(400, "图片尺寸过大")
    try:
        im.load()
    except Exception:
        raise HTTPException(400, bad_msg)
    return im

def process_image(raw: bytes):
    if len(raw) > MAX_IMG_BYTES:
        raise HTTPException(413, "单张图片太大，请换一张")
    if len(raw) < 50:
        raise HTTPException(400, "有图片无法识别，请换 JPG/PNG")
    im = _open_image(raw, POST_IMG_FMTS, "有图片无法识别，请换 JPG/PNG")
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

def _to_rgb(im):
    if im.mode in ("RGB", "L"):
        return im
    bg = Image.new("RGB", im.size, (255, 255, 255))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA"); bg.paste(im, mask=im.split()[-1])
    else:
        bg.paste(im.convert("RGB"))
    return bg

def process_avatar(raw: bytes):
    """校验格式与大小，按拍摄方向摆正、裁成正方形、缩到 256px，重编码为无 EXIF 的 JPEG。"""
    if len(raw) > AVATAR_MAX_BYTES:
        raise HTTPException(413, "头像不能超过 5MB，请换一张")
    im = _open_image(raw, POST_IMG_FMTS, "图片无法识别，请换 JPG、PNG 或 WebP")
    fmt = (im.format or "").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    if fmt not in AVATAR_FMTS:
        raise HTTPException(400, "头像只支持 JPG、PNG 或 WebP")
    if im.width * im.height > 50_000_000:
        raise HTTPException(400, "图片尺寸过大")
    im = ImageOps.exif_transpose(im)
    im = _to_rgb(im)
    w, h = im.size
    side = min(w, h)
    if side < 1:
        raise HTTPException(400, "图片尺寸过小")
    left, top = (w - side) // 2, (h - side) // 2
    im = im.crop((left, top, left + side, top + side))
    im = im.resize((AVATAR_SIDE, AVATAR_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)  # 不写入 EXIF
    return buf.getvalue()

_bot_av = None
def bot_default_avatar():
    """旅韩小助手默认头像：站点图标裁成 256 正方形 JPEG；没有图标文件时用纯色块。"""
    global _bot_av
    if _bot_av:
        return _bot_av
    for name in ("icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png"):
        path = os.path.join(STATIC, name)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    _bot_av = process_avatar(f.read())
                return _bot_av
            except Exception:
                pass
    im = Image.new("RGB", (AVATAR_SIDE, AVATAR_SIDE), (20, 110, 104))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True)
    _bot_av = buf.getvalue()
    return _bot_av

def has_avatar_row(r):
    return bool(r.get("has_avatar") or r.get("is_bot") or r.get("avatar"))

# ---------------- 页面 ----------------
def _esc_html(s):
    return html_lib.escape("" if s is None else str(s))

def _page(name, url_path=None, extra=None, ssr_target=None, ssr_html=""):
    from seo import html_file_response, FILE_PATHS
    return html_file_response(name, url_path=url_path or FILE_PATHS.get(name), extra=extra,
                              ssr_target=ssr_target, ssr_html=ssr_html)

def sitemap_public_posts(origin):
    """公开帖子（不含旅韩交友等登录可见频道）供 sitemap 使用。"""
    out = []
    if not _db_url():
        return out
    try:
        with db() as conn:
            rows = q(conn, """SELECT p.id, p.created_at FROM c_posts p JOIN c_users au ON au.id=p.user_id
                              WHERE NOT p.deleted AND p.status='published' AND NOT au.is_banned
                                AND p.board <> ALL(%s)
                              ORDER BY p.id DESC LIMIT 2000""", (list(LOGIN_ONLY_BOARDS),))
        for r in rows:
            last = r["created_at"]
            if last and getattr(last, "tzinfo", None):
                last = last.astimezone(datetime.timezone.utc).date().isoformat()
            elif last:
                last = last.date().isoformat()
            else:
                last = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
            out.append((origin + f"/community/post/{r['id']}", last))
    except Exception:
        return out
    return out

@router.get("/community")
def p_community():
    ssr = ""
    if _db_url():
        try:
            with db() as conn:
                rows = q(conn, """SELECT p.id, p.title, p.body, au.nickname FROM c_posts p
                                  JOIN c_users au ON au.id=p.user_id
                                  WHERE NOT p.deleted AND p.status='published' AND NOT au.is_banned
                                    AND p.board <> ALL(%s)
                                  ORDER BY p.id DESC LIMIT 20""", (list(LOGIN_ONLY_BOARDS),))
            items = []
            for r in rows:
                ex = (r["body"] or "").replace("\n", " ").strip()[:80]
                items.append(
                    f'<a class="post" href="/community/post/{r["id"]}"><div class="author"><div class="who">{_esc_html(r["nickname"])}</div></div>'
                    f'<h3>{_esc_html(r["title"])}</h3><p class="ex">{_esc_html(ex)}</p></a>'
                )
            if items:
                ssr = "".join(items)
        except Exception:
            ssr = ""
    return _page("community.html", "/community", ssr_target="list", ssr_html=ssr)

@router.get("/community/new")
def p_new(): return _page("community-new.html", "/community/new")

@router.get("/community/post/{pid}")
def p_post(pid: str):
    extra, ssr = None, ""
    try:
        n = int(pid) if re.fullmatch(r"\d{1,12}", pid or "") else 0
    except Exception:
        n = 0
    path = f"/community/post/{n}"
    if n and _db_url():
        try:
            with db() as conn:
                r = q(conn, """SELECT p.title, p.body, p.created_at, au.nickname
                               FROM c_posts p JOIN c_users au ON au.id=p.user_id
                               WHERE p.id=%s AND NOT p.deleted AND p.status='published' AND NOT au.is_banned
                                 AND p.board <> ALL(%s)""", (n, list(LOGIN_ONLY_BOARDS)), one=True)
            if r:
                desc = (r["body"] or "").replace("\n", " ").strip()[:140]
                dt = r["created_at"]
                date = dt.astimezone(datetime.timezone.utc).isoformat() if dt and getattr(dt, "tzinfo", None) else None
                extra = {"title": f"{r['title']} · 韩友圈 · 旅韩助手",
                         "description": desc or "韩友圈里关于韩国旅游的分享。",
                         "keywords": "韩国旅游攻略,韩友圈,旅韩助手",
                         "schema_type": "DiscussionForumPosting",
                         "author": r["nickname"], "date": date, "index": True}
                body = _esc_html(r["body"] or "").replace("\n", "<br>")
                ssr = (f'<h1>{_esc_html(r["title"])}</h1>'
                       f'<p class="meta">{_esc_html(r["nickname"])} · {_esc_html(_ts(r["created_at"]))}</p>'
                       f'<div class="body">{body}</div>')
        except Exception:
            extra, ssr = None, ""
    return _page("community-post.html", path, extra=extra, ssr_target="post", ssr_html=ssr)

@router.get("/login")
def p_login(): return _page("login.html", "/login")

@router.get("/member")
def p_member(): return _page("member.html", "/member")

@router.get("/rules")
def p_rules(): return _page("rules.html", "/rules")

@router.get("/admin")
def p_admin(): return _page("admin.html", "/admin")

@router.get("/community/boards")
def p_boards(): return _page("community-boards.html", "/community/boards")

@router.get("/community/rate")
def p_rate(): return _page("rate.html", "/community/rate")

@router.get("/community/friends")
def p_friends(): return _page("friends.html", "/community/friends")

@router.get("/community/friends/edit")
def p_friend_edit(): return _page("friend-edit.html", "/community/friends/edit")

@router.get("/community/friends/verify")
def p_buddy_verify(): return _page("buddy-verify.html", "/community/friends/verify")

@router.get("/community/friends/{uid}")
def p_friend(uid: str):
    n = int(uid) if re.fullmatch(r"\d{1,12}", uid or "") else 0
    return _page("friend.html", f"/community/friends/{n}", extra={"index": False})

@router.get("/community/messages")
def p_inbox(): return _page("messages.html", "/community/messages")

@router.get("/community/messages/{tid}")
def p_chat(tid: str):
    n = int(tid) if re.fullmatch(r"\d{1,12}", tid or "") else 0
    return _page("chat.html", f"/community/messages/{n}")

# ---------------- 账号 API ----------------
@router.get("/api/community/me")
def api_me(req: Request):
    with db() as conn:
        u = current_user(conn, req)
        st = get_settings(conn)
        if not u:
            return {"user": None, "boards": BOARDS, "merchant_boards": sorted(MERCHANT_BOARDS), "prices": st}
        disp = 3 if u["merchant"] else u["tier"]
        unread = q(conn, """SELECT count(*) AS n FROM c_messages m WHERE m.recipient_id=%s AND m.read_at IS NULL AND NOT m.deleted
                            AND NOT EXISTS (SELECT 1 FROM c_blocks b WHERE b.blocker_id=%s AND b.blocked_id=m.sender_id)""",
                   (u["id"], u["id"]), one=True)["n"]
        has_profile = q(conn, "SELECT 1 AS x FROM c_dating_profiles WHERE user_id=%s", (u["id"],), one=True) is not None
        from points import user_points_summary
        pts = user_points_summary(conn, u["id"])
        from merch import consult_unread
        cun = consult_unread(conn, u["id"])
        from retention import notif_unread, badges_out
        nun = notif_unread(conn, u["id"])
        ub = q(conn, "SELECT COALESCE(is_official,FALSE) AS o, (SELECT string_agg(badge, ',' ORDER BY created_at) FROM c_badges WHERE user_id=%s) AS b FROM c_users WHERE id=%s", (u["id"], u["id"]), one=True)
        return {"user": {"id": u["id"], "member_no": u.get("member_no") or u["id"], "nickname": u["nickname"], "tier": u["tier"], "base_tier": u["base_tier"],
                         "display_tier": disp, "tier_name": TIER_NAMES[disp], "is_member": u["tier"] >= 1,
                         "merchant": bool(u["merchant"]), "merchant_info": merchant_quota(conn, u),
                         "merchant_expired": bool(u["merchant_until"] and not u["merchant"]),
                         "account": mask_account(u), "joined": _ts(u["created_at"]),
                         "verified": bool(u.get("verified")), "id_verified": bool(u.get("id_verified")), "unread": unread, "consult_unread": cun, "notif_unread": nun, "official": bool(ub["o"]), "badges": badges_out(ub["b"]), "has_profile": has_profile,
                         "has_avatar": bool(u.get("has_avatar")),
                         "points_name": pts["name"], "points_balance": pts["balance"],
                         "points_streak": pts["streak"], "coupon_available": pts["coupon_available"]},
                "boards": BOARDS, "merchant_boards": sorted(MERCHANT_BOARDS), "prices": st}

@router.get("/api/community/prices")
def api_prices():
    with db() as conn:
        st = get_settings(conn)
    return {k: st[k] for k in PRICE_KEYS}

@router.get("/api/community/my-posts")
def api_my_posts(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        rows = q(conn, """SELECT id,board,title,status,review_note,created_at FROM c_posts
                          WHERE user_id=%s AND NOT deleted ORDER BY id DESC LIMIT 50""", (u["id"],))
    return {"posts": [{"id": r["id"], "board_name": BOARDS.get(r["board"], ""), "title": r["title"], "status": r["status"],
                       "note": r["review_note"], "time": _ts(r["created_at"])} for r in rows]}

@router.get("/api/community/avatar/{uid}")
def api_avatar(uid: int):
    """公开头像：有自定义图则返回 JPEG；旅韩小助手无自定义时用站点图标。"""
    with db() as conn:
        r = q(conn, "SELECT avatar, is_bot, is_banned FROM c_users WHERE id=%s", (uid,), one=True)
    if not r or r["is_banned"]:
        raise HTTPException(404, "没有头像")
    data = bytes(r["avatar"]) if r["avatar"] else (bot_default_avatar() if r["is_bot"] else None)
    if not data:
        raise HTTPException(404, "没有头像")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=120"})

@router.post("/api/community/avatar")
async def api_set_avatar(req: Request, image: UploadFile = File(...)):
    csrf(req)
    with db() as conn:
        need_user(conn, req)
    raw = await image.read(AVATAR_MAX_BYTES + 1)
    with db() as conn:
        u = need_user(conn, req)
        if is_bot_uid(conn, u["id"]):
            raise HTTPException(403, "系统账号不可修改")
        limit("av:" + str(u["id"]), 8, 3600, "更换头像太频繁，请一小时后再试")
        limit("avip:" + ip_of(req), 20, 3600, "更换头像太频繁，请一小时后再试")
        data = process_avatar(raw)
        q(conn, "UPDATE c_users SET avatar=%s WHERE id=%s", (data, u["id"]))
    return {"ok": True, "has_avatar": True}

@router.delete("/api/community/avatar")
def api_del_avatar(req: Request):
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        if is_bot_uid(conn, u["id"]):
            raise HTTPException(403, "系统账号不可修改")
        q(conn, "UPDATE c_users SET avatar=NULL WHERE id=%s", (u["id"],))
    return {"ok": True, "has_avatar": False}

@router.post("/api/community/register")
async def api_register(req: Request, resp: Response):
    csrf(req)
    limit("reg:" + ip_of(req), 5, 3600, "注册太频繁，请一小时后再试")
    limit("reg-day:" + ip_of(req), 10, 86400, "今天注册次数太多，请明天再试")
    limit("reg-global", 300, 3600, "注册人数太多，请稍后再试")
    d = await _json(req)
    kind, acct = parse_account(str(d.get("account") or ""))
    pw = str(d.get("password") or "")
    if len(pw) < 8 or len(pw) > 128:
        raise HTTPException(400, "密码需 8–128 位")
    if pw.isdigit() and len(set(pw)) <= 2:
        raise HTTPException(400, "密码太简单，请换一个")
    nick = clean_text(d.get("nickname"), 16, "昵称", 2)
    if re.search(r"管理员|官方|admin|旅韩小助手", nick, re.I):
        raise HTTPException(400, "昵称不能包含“管理员/官方/旅韩小助手”")
    if not d.get("agree"):
        raise HTTPException(400, "请先同意社区规则")
    with db() as conn:
        exists = q(conn, f"SELECT id FROM c_users WHERE {kind}=%s", (acct,), one=True)
        if exists:
            raise HTTPException(409, "这个账号已注册，请直接登录")
        ref = resolve_ref(conn, req, d.get("promoter"))
        row = q(conn, f"INSERT INTO c_users({kind},nickname,pw_hash,last_login,promoter_code) VALUES (%s,%s,%s,now(),%s) RETURNING id",
                (acct, nick, hash_pw(pw), ref), one=True)
        assign_member_no(conn, row["id"])
        from points import issue_welcome_coupon
        issue_welcome_coupon(conn, row["id"])
        from opening_promo import on_new_user
        on_new_user(conn, row["id"])
        new_session(conn, resp, req, user_id=row["id"])
    return {"ok": True}

@router.post("/api/community/login")
async def api_login(req: Request, resp: Response):
    csrf(req)
    limit("login:" + ip_of(req), 10, 600, "登录尝试太多，请十分钟后再试")
    d = await _json(req)
    kind, acct = parse_account(str(d.get("account") or ""))
    pw = str(d.get("password") or "")[:256]
    limit("login-acct:" + acct, 8, 900, "这个账号尝试次数太多，请 15 分钟后再试")
    with db() as conn:
        u = q(conn, f"SELECT id,pw_hash,is_banned,is_bot FROM c_users WHERE {kind}=%s", (acct,), one=True)
        if not u:
            dummy_check(pw)
            raise HTTPException(401, "账号或密码不对")
        if u.get("is_bot") or not check_pw(pw, u["pw_hash"]):
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
    d = await _json(req)
    with db() as conn:
        u = need_user(conn, req)
        limit("pw:" + str(u["id"]), 5, 3600)
        row = q(conn, "SELECT pw_hash FROM c_users WHERE id=%s", (u["id"],), one=True)
        if not check_pw(str(d.get("old") or "")[:256], row["pw_hash"]):
            raise HTTPException(400, "原密码不对")
        new = str(d.get("new") or "")
        if len(new) < 8 or len(new) > 128:
            raise HTTPException(400, "新密码需 8–128 位")
        if new.isdigit() and len(set(new)) <= 2:
            raise HTTPException(400, "密码太简单，请换一个")
        q(conn, "UPDATE c_users SET pw_hash=%s WHERE id=%s", (hash_pw(new), u["id"]))
        q(conn, "DELETE FROM c_sessions WHERE user_id=%s AND token_hash<>%s", (u["id"], _th(req.cookies.get(COOKIE))))
    return {"ok": True}


@router.post("/api/community/account/delete")
async def api_delete_account(req: Request, resp: Response):
    """用户自助注销：验证密码后彻底删除账号及其帖子、评论、图片、交友资料、私信、头像和登录会话。"""
    csrf(req)
    d = await _json(req)
    if str(d.get("confirm") or "") != "注销":
        raise HTTPException(400, "请输入“注销”两个字确认")
    with db() as conn:
        u = need_user(conn, req)
        limit("del:" + str(u["id"]), 5, 3600, "尝试太多，请稍后再试")
        if is_bot_uid(conn, u["id"]):
            raise HTTPException(403, "系统账号不可注销")
        row = q(conn, "SELECT pw_hash FROM c_users WHERE id=%s", (u["id"],), one=True)
        if not check_pw(str(d.get("password") or "")[:256], row["pw_hash"]):
            raise HTTPException(400, "密码不对")
        delete_user_data(conn, u["id"])
    resp.delete_cookie(COOKIE, path="/")
    return {"ok": True}

def delete_user_data(conn, uid):
    """彻底删除（不是隐藏）：外键 ON DELETE CASCADE 会删掉帖子、图片、评论、点赞、交友资料、照片、私信、会话；
    举报和推广佣金记录里的用户 ID 置空，只保留不含个人信息的统计。"""
    pids = q(conn, "SELECT DISTINCT post_id FROM c_comments WHERE user_id=%s", (uid,))
    liked = q(conn, "SELECT DISTINCT post_id FROM c_likes WHERE user_id=%s", (uid,))
    q(conn, "DELETE FROM c_users WHERE id=%s", (uid,))
    for p in pids:
        _recount(conn, p["post_id"])
    for p in liked:
        q(conn, "UPDATE c_posts SET like_count=(SELECT count(*) FROM c_likes WHERE post_id=%s) WHERE id=%s",
          (p["post_id"], p["post_id"]))


# ---------------- 旅韩小助手（AI 自动回复）----------------
_ai_lock = threading.Lock()
_ai_day = {"date": None, "global": 0, "user": None}
_ai_user_hits = defaultdict(deque)
_ai_threads = []
_ai_threads_lock = threading.Lock()

def assign_member_no(conn, uid):
    """给用户分配未被占用的会员编号：默认用 id；若 id 已被别人当靓号占用，则取其后第一个空号。"""
    q(conn, """UPDATE c_users SET member_no = (
         SELECT s FROM generate_series(%s, %s + 1000000) s
         WHERE NOT EXISTS (SELECT 1 FROM c_users u WHERE u.member_no = s)
         LIMIT 1
       ) WHERE id=%s AND member_no IS NULL""", (uid, uid, uid))


def ensure_bot(conn):
    """建表时创建系统账号；无可用密码、无手机号/邮箱，不能登录。"""
    row = q(conn, "SELECT id, member_no FROM c_users WHERE is_bot LIMIT 1", one=True)
    if row:
        q(conn, "UPDATE c_users SET nickname=%s, pw_hash=%s, is_banned=FALSE WHERE id=%s AND (nickname<>%s OR pw_hash<>%s OR is_banned)",
          (BOT_NICK, BOT_PW_HASH, row["id"], BOT_NICK, BOT_PW_HASH))
        if not row.get("member_no"):
            assign_member_no(conn, row["id"])
        return row
    row = q(conn, """INSERT INTO c_users(nickname,pw_hash,is_bot,is_member,tier,verified)
                      VALUES (%s,%s,TRUE,FALSE,0,FALSE) RETURNING id""",
             (BOT_NICK, BOT_PW_HASH), one=True)
    assign_member_no(conn, row["id"])
    return row

def bot_id(conn):
    r = ensure_bot(conn)
    return r["id"] if r else None

def is_bot_uid(conn, uid):
    r = q(conn, "SELECT is_bot FROM c_users WHERE id=%s", (uid,), one=True)
    return bool(r and r["is_bot"])

def ai_enabled(conn):
    return get_settings(conn).get("ai_reply", "1") == "1"

def _ai_today():
    return datetime.datetime.now(KST).date().isoformat()

def _ai_allow(user_id):
    """每日全站上限 + 单用户频率。超限返回 False，不抛错。"""
    now = time.time()
    today = _ai_today()
    with _ai_lock:
        if _ai_day["date"] != today:
            _ai_day["date"] = today
            _ai_day["global"] = 0
            _ai_day["user"] = defaultdict(int)
        if _ai_day["user"] is None:
            _ai_day["user"] = defaultdict(int)
        if _ai_day["global"] >= AI_DAILY_LIMIT:
            return False
        if user_id and _ai_day["user"][user_id] >= AI_USER_DAY:
            return False
        dq = _ai_user_hits[user_id or 0]
        while dq and now - dq[0] > 3600:
            dq.popleft()
        if len(dq) >= AI_USER_HOUR:
            return False
        dq.append(now)
        _ai_day["global"] += 1
        if user_id:
            _ai_day["user"][user_id] += 1
        return True

def looks_like_question(text):
    t = text or ""
    if re.search(r"[？?]", t):
        return True
    return bool(re.search(r"(吗|呢|怎么|如何|哪里|哪儿|哪裡|多少|要不要|可不可以|请问|求问|有没有|有木有|怎么办|哪家|推荐哪)", t))

def comment_wants_ai(comment_body, comment_user_id, post_user_id):
    """评论自动回复：@旅韩小助手，或帖主在评论里提问时才回。"""
    body = comment_body or ""
    if re.search(r"@?旅韩小助手", body):
        return True
    return comment_user_id == post_user_id and looks_like_question(body)

def sanitize_ai_text(text):
    """清洗模型输出：限长、去掉不应出现的品牌/带货/联系方式。不记录原文密钥。"""
    if not isinstance(text, str):
        return ""
    s = text.strip()
    s = re.sub(r'^["「『]+|["」』]+$', "", s).strip()
    s = re.sub(r"```+", "", s)
    # 屏蔽不应出现的品牌与带货词（大小写不敏感；中文直接替换）
    for w in ("奋韩", "奮韓", "抖音", "直播间", "直播間", "Douyin", "douyin", "TikTok", "tiktok"):
        s = re.sub(re.escape(w), "", s, flags=re.I)
    s = re.sub(r"(微信|weixin|wechat|v信|威信|qq|电话|手機|手机|邮箱|email)[:：]?\s*\S+", "", s, flags=re.I)
    s = re.sub(r"https?://\S+", "", s, flags=re.I)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    if len(s) > AI_MAX_CHARS:
        s = s[:AI_MAX_CHARS].rstrip()
        s = re.sub(r"[，,、；;：:\s]+$", "", s)
    return s

def _deepseek_key():
    return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

def ai_chat(system, user, timeout=AI_TIMEOUT):
    """调用 DeepSeek 文本接口。失败返回空字符串；不打印密钥或响应体。"""
    key = _deepseek_key()
    if not key:
        return ""
    import httpx
    body = {"model": DEEPSEEK_MODEL, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], "max_tokens": 400, "temperature": 0.5, "thinking": {"type": "disabled"}}
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{DEEPSEEK_API}/chat/completions", json=body,
                       headers={"Authorization": "Bearer " + key})
    except Exception:
        print("ai reply network error", flush=True)
        return ""
    if r.status_code != 200:
        print("ai upstream error", r.status_code, flush=True)
        return ""
    try:
        txt = (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception:
        print("ai reply parse error", flush=True)
        return ""
    return sanitize_ai_text(txt)

def _ai_user_prompt(board, title, body, comment=None):
    bname = BOARDS.get(board, board or "")
    msg = "频道：" + bname + "\n标题：" + (title or "（无）") + "\n正文：\n" + (body or "（无）")
    if comment:
        msg += "\n\n请针对下面这条用户评论作补充回复：\n" + comment
    else:
        msg += "\n\n请针对这篇新帖写一条对原作者和其他读者都有用的短评。"
    return msg

def _insert_bot_comment(conn, post_id, text):
    text = sanitize_ai_text(text)
    if not text:
        return None
    bid = bot_id(conn)
    if not bid:
        return None
    row = q(conn, "INSERT INTO c_comments(post_id,user_id,body) VALUES (%s,%s,%s) RETURNING id",
            (post_id, bid, text), one=True)
    _recount(conn, post_id)
    return row["id"] if row else None

def _ai_job(kind, post_id, trigger_user_id, comment_body=None):
    """后台任务：失败只记日志，不影响发帖。"""
    try:
        if not _deepseek_key():
            return
        with db() as conn:
            if not ai_enabled(conn):
                return
            p = q(conn, """SELECT p.id,p.title,p.body,p.board,p.status,p.deleted,p.user_id
                           FROM c_posts p WHERE p.id=%s""", (post_id,), one=True)
            if not p or p["deleted"] or p["status"] != "published":
                return
            if is_bot_uid(conn, p["user_id"]):
                return
            bid = bot_id(conn)
            if kind == "post":
                already = q(conn, "SELECT id FROM c_comments WHERE post_id=%s AND user_id=%s AND NOT deleted LIMIT 1",
                            (post_id, bid), one=True)
                if already:
                    return
            elif kind == "comment":
                if not comment_wants_ai(comment_body or "", trigger_user_id, p["user_id"]):
                    return
                last = q(conn, """SELECT user_id FROM c_comments WHERE post_id=%s AND NOT deleted
                                  ORDER BY id DESC LIMIT 1""", (post_id,), one=True)
                if last and last["user_id"] == bid:
                    return
            if not _ai_allow(trigger_user_id):
                print("ai reply skipped: rate limit", flush=True)
                return
        text = ai_chat(AI_SYSTEM, _ai_user_prompt(p["board"], p["title"], p["body"], comment_body))
        if not text:
            return
        with db() as conn:
            if not ai_enabled(conn):
                return
            p2 = q(conn, "SELECT id,deleted,status FROM c_posts WHERE id=%s", (post_id,), one=True)
            if not p2 or p2["deleted"] or p2["status"] != "published":
                return
            _insert_bot_comment(conn, post_id, text)
    except Exception:
        print("ai reply failed", kind, flush=True)

def wait_ai_replies(timeout=8):
    """测试用：等待后台 AI 线程结束。"""
    deadline = time.time() + timeout
    while True:
        with _ai_threads_lock:
            alive = [th for th in _ai_threads if th.is_alive()]
            _ai_threads[:] = alive
        if not alive:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.05)

def schedule_ai_reply(kind, post_id, trigger_user_id, comment_body=None):
    if not post_id or not _deepseek_key():
        return
    th = threading.Thread(target=_ai_job, args=(kind, post_id, trigger_user_id, comment_body),
                          name="ai-reply", daemon=True)
    with _ai_threads_lock:
        _ai_threads[:] = [t for t in _ai_threads if t.is_alive()]   # 清理已结束线程，避免列表无限增长
        _ai_threads.append(th)
    th.start()


IMG_COLS = """(SELECT id FROM c_images i WHERE i.post_id=p.id ORDER BY pos LIMIT 1) AS cover,
                 (SELECT count(*) FROM c_images i WHERE i.post_id=p.id) AS img_n,
                 (SELECT ARRAY(SELECT id FROM c_images i WHERE i.post_id=p.id ORDER BY pos LIMIT 3)) AS thumbs"""

def _with_images(o, r):
    o["cover"] = r["cover"]
    o["img_n"] = r["img_n"] or 0
    thumbs = r.get("thumbs") or []
    if thumbs and not isinstance(thumbs, (list, tuple)):
        thumbs = list(thumbs)
    o["thumbs"] = [int(x) for x in thumbs] if thumbs else ([int(r["cover"])] if r.get("cover") else [])
    return o

# ---------------- 帖子 API ----------------
def _post_out(r, full=False):
    o = {"id": r["id"], "board": r["board"], "board_name": BOARDS.get(r["board"], ""), "title": r["title"],
         "author": r["nickname"], "author_id": r.get("user_id") or r.get("author_id"),
         "author_tier": r["tier"], "has_avatar": has_avatar_row(r), "likes": r["like_count"],
         "comments": r["comment_count"], "time": _ts(r["created_at"]),
         "merchant": bool(r.get("is_merchant")), "status": r.get("status", "published"),
         "views": r.get("views") or 0,
         "pinned": bool(r.get("pinned")), "featured": bool(r.get("featured")), "topic": bool(r.get("topic")),
         "author_official": bool(r.get("is_official"))}
    mc = r.get("merchant_cat") if r.get("merchant_cat") in MERCHANT_CAT_KEYS else None
    if not mc and r.get("is_merchant"):
        mc = BOARD_TO_MERCHANT_CAT.get(r.get("board"), "other")
    if mc:
        o["merchant_cat"] = mc
        o["merchant_cat_name"] = MERCHANT_CAT_LABELS.get(mc, "")
    if r.get("badges"):
        from retention import badges_out
        o["author_badges"] = badges_out(r["badges"])
    if r.get("tag"):
        from merch import tag_name
        o["tag"] = r["tag"]; o["tag_name"] = tag_name(r["board"], r["tag"])
    if r.get("offer"):
        o["offer"] = r["offer"]
    if r.get("valid_until"):
        o["valid_until"] = str(r["valid_until"])
    if full:
        o["body"] = r["body"]
    else:
        b = r["body"].replace("\n", " ")
        o["excerpt"] = b[:80] + ("…" if len(b) > 80 else "")
    return o

@router.get("/api/community/posts")
def api_posts(req: Request, board: str = "", before: int = 0, limit_n: int = 20, tag: str = ""):
    limit_n = max(1, min(limit_n, 30))
    if board and board not in BOARDS:
        raise HTTPException(400, "频道不存在")
    with db() as conn:
        u = current_user(conn, req)
        tier = u["tier"] if u else 0
        if board in LOGIN_ONLY_BOARDS and tier < 1:
            return {"posts": [], "login_required": not u, "tier_required": 1}
        cond, args = ["NOT p.deleted", "p.status='published'", "NOT au.is_banned"], []
        if board:
            cond.append("p.board=%s"); args.append(board)
            if tag and re.fullmatch(r"[a-z]{2,12}", tag):
                cond.append("p.tag=%s"); args.append(tag)
        elif tier < 1:
            cond.append("p.board <> ALL(%s)"); args.append(list(LOGIN_ONLY_BOARDS))
        pinned_rows = []
        if board and before <= 0 and not tag:
            pinned_rows = q(conn, f"""SELECT p.*, {AUTHOR_COLS}, (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier,
                 (au.avatar IS NOT NULL OR COALESCE(au.is_bot, FALSE)) AS has_avatar,
                 {IMG_COLS}
                 FROM c_posts p JOIN c_users au ON au.id=p.user_id
                 WHERE {' AND '.join(cond)} AND p.pinned ORDER BY p.pinned_at DESC NULLS LAST LIMIT 3""", tuple(args))
        if board and not tag:
            cond.append("NOT p.pinned")
        if before > 0:
            cond.append("p.id<%s"); args.append(before)
        rows = q(conn, f"""SELECT p.*, {AUTHOR_COLS}, (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier,
                 (au.avatar IS NOT NULL OR COALESCE(au.is_bot, FALSE)) AS has_avatar,
                 {IMG_COLS}
                 FROM c_posts p JOIN c_users au ON au.id=p.user_id
                 WHERE {' AND '.join(cond)} ORDER BY p.id DESC LIMIT %s""", (*args, limit_n + 1))
    more = len(rows) > limit_n
    out = []
    for r in rows[:limit_n]:
        o = _with_images(_post_out(r), r); out.append(o)
    pins = []
    for r in pinned_rows:
        o = _with_images(_post_out(r), r); pins.append(o)
    return {"posts": out, "more": more, "pinned": pins}

FEED_TABS = ("recommend", "news", "hot", "latest", "follow", "featured", "topic")

@router.get("/api/community/feed")
def api_feed(req: Request, tab: str = "recommend", boards: str = "", offset: int = 0, limit_n: int = 20):
    """首页信息流：精选 / 实用干货 / 今日热议 / 刚发布 / 我关注的（关注=关注的用户 + 关注的频道）"""
    if tab not in FEED_TABS:
        raise HTTPException(400, "标签不存在")
    limit_n = max(1, min(limit_n, 30))
    offset = max(0, min(offset, 300))
    bl = [b for b in boards.split(",") if b][:12]
    if any(b not in BOARDS for b in bl):
        raise HTTPException(400, "频道不存在")
    with db() as conn:
        u = current_user(conn, req)
        tier = u["tier"] if u else 0
        if tab == "follow" and not u:
            return {"posts": [], "more": False, "login_required": True}
        cond, args = ["NOT p.deleted", "p.status='published'", "NOT au.is_banned"], []
        if tier < 1:
            cond.append("p.board <> ALL(%s)"); args.append(list(LOGIN_ONLY_BOARDS))
        if bl:
            cond.append("p.board = ANY(%s)"); args.append(bl)
        if u:
            cond.append("NOT EXISTS (SELECT 1 FROM c_blocks b WHERE b.blocker_id=%s AND b.blocked_id=p.user_id)"); args.append(u["id"])
        if tab == "news":
            cond.append("p.board = ANY(%s)"); args.append(NEWS_BOARDS)
        if tab == "featured":
            cond.append("p.featured")
        if tab == "topic":
            cond.append("p.topic")
        if tab == "follow":
            cond.append("""(p.user_id IN (SELECT followee_id FROM c_follows WHERE follower_id=%s)
                           OR p.board IN (SELECT board FROM c_board_follows WHERE user_id=%s))""")
            args += [u["id"], u["id"]]
        score, order = "0", "p.id DESC"
        if tab == "recommend":
            score = """((p.like_count*2 + p.comment_count*3 + 1 + CASE WHEN EXISTS (SELECT 1 FROM c_images i WHERE i.post_id=p.id) THEN 1 ELSE 0 END)
                       / power(EXTRACT(EPOCH FROM (now()-p.created_at))/3600 + 2, 1.3)
                       * (CASE WHEN p.featured THEN 3 ELSE 1 END))"""
            order = "score DESC, p.id DESC"
        elif tab == "hot":
            score = "(p.like_count + p.comment_count*2)"
            order = "score DESC, p.id DESC"
        elif tab == "featured":
            order = "p.featured_at DESC NULLS LAST, p.id DESC"

        def run(extra, eargs):
            return q(conn, f"""SELECT p.*, {AUTHOR_COLS}, au.verified, (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier,
                     (au.avatar IS NOT NULL OR COALESCE(au.is_bot, FALSE)) AS has_avatar,
                     {score} AS score,
                     {IMG_COLS}
                     FROM c_posts p JOIN c_users au ON au.id=p.user_id
                     WHERE {' AND '.join(cond + extra)} ORDER BY {order} LIMIT %s OFFSET %s""",
                     (*args, *eargs, limit_n + 1, offset))
        fallback = False
        if tab == "recommend":
            rows = run(["p.created_at > now() - make_interval(days => %s)"], [RECOMMEND_DAYS])
            if not rows and offset == 0:
                order = "p.id DESC"; rows = run([], []); fallback = True
        elif tab == "hot":
            if offset > 0 and req.query_params.get("fb") == "1":   # 第一页已退回 7 天时，后续页保持一致
                rows = []
            else:
                rows = run(["p.created_at > now() - make_interval(hours => %s)"], [HOT_HOURS])
            if not rows and (offset == 0 or req.query_params.get("fb") == "1"):
                rows = run(["p.created_at > now() - make_interval(days => %s)"], [HOT_FALLBACK_DAYS]); fallback = True
        else:
            rows = run([], [])
    more = len(rows) > limit_n
    out = []
    for r in rows[:limit_n]:
        o = _with_images(_post_out(r), r); o["author_verified"] = bool(r["verified"]); out.append(o)
    return {"posts": out, "more": more, "fallback": fallback, "next": offset + limit_n}

@router.get("/api/community/boards")
def api_boards(req: Request):
    """全部频道：分组 + 频道 + 当前用户已关注的频道（未配置数据库时也能返回分组）"""
    cats = [{"key": c["key"], "name": c["name"], "icon": c["icon"],
             "boards": [{"key": b, "name": BOARDS[b], "icon": BOARD_ICONS.get(b, "📌"), "desc": BOARD_DESCS.get(b, ""), "merchant": b in MERCHANT_BOARDS,
                         "members_only": b in LOGIN_ONLY_BOARDS} for b in c["boards"]]} for c in BOARD_CATEGORIES]
    joined, counts = [], {}
    if _db_url():
        with db() as conn:
            u = current_user(conn, req)
            if u:
                joined = [r["board"] for r in q(conn, "SELECT board FROM c_board_follows WHERE user_id=%s", (u["id"],))]
            for r in q(conn, "SELECT board, count(*) AS n FROM c_board_follows GROUP BY board"):
                counts[r["board"]] = r["n"]
    for c in cats:
        for b in c["boards"]:
            b["joined"] = b["key"] in joined
            b["members"] = counts.get(b["key"], 0)
    return {"categories": cats, "joined": joined}

@router.post("/api/community/boards/{board}/join")
def api_board_join(req: Request, board: str):
    csrf(req)
    if board not in BOARDS:
        raise HTTPException(404, "频道不存在")
    with db() as conn:
        u = need_user(conn, req)
        limit("bj:" + str(u["id"]), 60, 600)
        had = q(conn, "DELETE FROM c_board_follows WHERE user_id=%s AND board=%s RETURNING board", (u["id"], board), one=True)
        if not had:
            q(conn, "INSERT INTO c_board_follows(user_id,board) VALUES (%s,%s) ON CONFLICT DO NOTHING", (u["id"], board))
    return {"joined": not had}

@router.post("/api/community/follow/{uid}")
def api_follow(req: Request, uid: int):
    """关注/取消关注某个用户（关注后其帖子出现在“关注”标签）"""
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        if uid == u["id"]:
            raise HTTPException(400, "不能关注自己")
        if not q(conn, "SELECT id FROM c_users WHERE id=%s AND NOT is_banned", (uid,), one=True):
            raise HTTPException(404, "用户不存在")
        limit("fo:" + str(u["id"]), 60, 600)
        had = q(conn, "DELETE FROM c_follows WHERE follower_id=%s AND followee_id=%s RETURNING followee_id", (u["id"], uid), one=True)
        if not had:
            q(conn, "INSERT INTO c_follows(follower_id,followee_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (u["id"], uid))
            from retention import on_follow
            on_follow(conn, u, uid)
    return {"following": not had}

@router.get("/api/community/rate")
def api_rate():
    """汇率换算用的参考汇率：管理员在后台手动设置；数据库未配置时用默认值"""
    v = DEFAULT_SETTINGS["rate_cny_krw"]
    updated = None
    if _db_url():
        try:
            with db() as conn:
                v = get_settings(conn)["rate_cny_krw"]
                row = q(conn, "SELECT updated_at FROM c_settings WHERE key=%s", ("rate_cny_krw",), one=True)
                if row and row.get("updated_at"):
                    updated = _ts(row["updated_at"])
        except HTTPException:
            pass
    out = {"cny_krw": float(v), "source": "manual"}
    if updated:
        out["updated_at"] = updated
    return out

@router.get("/api/community/posts/{pid}")
def api_post(req: Request, pid: int):
    with db() as conn:
        u = current_user(conn, req)
        r = q(conn, f"""SELECT p.*, {AUTHOR_COLS}, au.verified, (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier,
                              (au.avatar IS NOT NULL OR COALESCE(au.is_bot, FALSE)) AS has_avatar,
                              (au.merchant_until IS NOT NULL AND au.merchant_until>now()) AS author_merchant
                       FROM c_posts p JOIN c_users au ON au.id=p.user_id
                       WHERE p.id=%s AND NOT p.deleted AND NOT au.is_banned""", (pid,), one=True)
        if not r or (r["status"] != "published" and not (u and u["id"] == r["user_id"])):
            raise HTTPException(404, "帖子不存在或已删除")
        if r["status"] == "published" and not (u and u["id"] == r["user_id"]):
            vk = "pv:%s:%d" % ((u or {}).get("id") or ip_of(req), pid)
            try:
                limit(vk, 1, 1800)
                q(conn, "UPDATE c_posts SET views=views+1 WHERE id=%s", (pid,))
                r["views"] = (r.get("views") or 0) + 1
            except HTTPException:
                pass
        if r["board"] in LOGIN_ONLY_BOARDS and not u:
            raise HTTPException(401, "旅韩交友频道需登录并开通会员后查看")
        if r["board"] in LOGIN_ONLY_BOARDS and u["tier"] < 1:
            raise HTTPException(403, "旅韩交友频道需开通会员（¥%s）后查看" % price(conn, "price_t1"))
        imgs = q(conn, "SELECT id,width,height FROM c_images WHERE post_id=%s ORDER BY pos", (pid,))
        cms = q(conn, f"""SELECT c.id,c.body,c.created_at,c.user_id,{AUTHOR_COLS},au.is_bot,
                                 (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier,
                                 (au.avatar IS NOT NULL OR COALESCE(au.is_bot, FALSE)) AS has_avatar
                         FROM c_comments c
                         JOIN c_users au ON au.id=c.user_id
                         WHERE c.post_id=%s AND NOT c.deleted AND NOT au.is_banned ORDER BY c.id LIMIT 500""", (pid,))
        liked = bool(u and q(conn, "SELECT 1 AS x FROM c_likes WHERE post_id=%s AND user_id=%s", (pid, u["id"]), one=True))
        following = bool(u and q(conn, "SELECT 1 AS x FROM c_follows WHERE follower_id=%s AND followee_id=%s", (u["id"], r["user_id"]), one=True))
    o = _post_out(r, full=True)
    o["author_id"] = r["user_id"]
    o["author_verified"] = bool(r["verified"])
    o["following"] = following
    o["images"] = [{"id": i["id"], "w": i["width"], "h": i["height"]} for i in imgs]
    o["liked"] = liked
    o["mine"] = bool(u and u["id"] == r["user_id"])
    o["comment_tier"] = 2 if r["board"] in LOGIN_ONLY_BOARDS else 1
    o["review_note"] = r.get("review_note")
    from merch import MEDICAL_BOARDS, MEDICAL_NOTE, post_offers_consult
    o["consult"] = not o["mine"] and r["status"] == "published" and post_offers_consult(r)
    if r["board"] in MEDICAL_BOARDS:
        o["medical_note"] = MEDICAL_NOTE
    o["comment_list"] = [{"id": c["id"], "body": c["body"], "author": c["nickname"], "author_id": c["user_id"],
                          "author_tier": c["tier"], "has_avatar": has_avatar_row(c),
                          "time": _ts(c["created_at"]), "mine": bool(u and u["id"] == c["user_id"]),
                          "is_bot": bool(c.get("is_bot")), "official": bool(c.get("is_official")),
                          "badges": (__import__("retention").badges_out(c.get("badges")))} for c in cms]
    return o


def _merchant_cat_clause(cat):
    """只读分类过滤。返回 (sql, args)。cat 已是合法入库 key 或 'all'/空。"""
    if not cat or cat == "all":
        return "", []
    mapped = [b for b, c in BOARD_TO_MERCHANT_CAT.items() if c == cat]
    if cat == "other":
        mapped_boards = list(BOARD_TO_MERCHANT_CAT.keys())
        return (" AND (p.merchant_cat='other' OR ((p.merchant_cat IS NULL OR p.merchant_cat='') AND p.board <> ALL(%s)))",
                [mapped_boards])
    if mapped:
        return (" AND (p.merchant_cat=%s OR ((p.merchant_cat IS NULL OR p.merchant_cat='') AND p.board=%s))",
                [cat, mapped[0]])
    return " AND p.merchant_cat=%s", [cat]


@router.get("/api/community/merchant-feed")
def api_merchant_feed(req: Request, cat: str = "", before: int = 0, limit_n: int = 20):
    limit_n = max(1, min(limit_n, 30))
    cat = (cat or "").strip()
    if cat and cat != "all" and cat not in MERCHANT_CAT_KEYS:
        raise HTTPException(400, "分类不存在")
    extra, extra_args = _merchant_cat_clause(cat)
    with db() as conn:
        u = current_user(conn, req)
        uid = u["id"] if u else None
        args = list(extra_args)
        before_sql = ""
        if before > 0:
            before_sql = " AND p.id<%s"
            args.append(before)
        args.append(limit_n + 1)
        rows = q(conn, f"""SELECT p.*, {AUTHOR_COLS}, (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier,
                 (au.avatar IS NOT NULL OR COALESCE(au.is_bot, FALSE)) AS has_avatar,
                 {IMG_COLS}
                 FROM c_posts p JOIN c_users au ON au.id=p.user_id
                 WHERE NOT p.deleted AND p.status='published' AND p.is_merchant AND NOT au.is_banned
                 {extra}{before_sql}
                 ORDER BY p.id DESC LIMIT %s""", tuple(args))
    has_more = len(rows) > limit_n
    rows = rows[:limit_n]
    posts = []
    for r in rows:
        o = _with_images(_post_out(r), r)
        o["consult"] = (uid is None) or (uid != (r.get("user_id") or r.get("author_id")))
        o["mine"] = bool(uid and uid == (r.get("user_id") or r.get("author_id")))
        posts.append(o)
    cats = [{"key": "all", "name": "全部"}] + [{"key": k, "name": n} for k, n in MERCHANT_CATS]
    return {"posts": posts, "has_more": has_more, "cats": cats}


@router.post("/api/community/posts")
async def api_new_post(req: Request, board: str = Form(...), title: str = Form(...), body: str = Form(...),
                       tag: str = Form(""), offer: str = Form(""), valid_until: str = Form(""),
                       merchant_cat: str = Form(""),
                       images: list[UploadFile] = File(default=[])):
    csrf(req)
    if board not in BOARDS:
        raise HTTPException(400, "请选择频道")
    daily = board == DAILY_BOARD
    title = clean_text(title, 40, "标题", 2 if daily else 4)
    body = clean_text(body, 3000, "正文", 5 if daily else 10)
    images = [f for f in (images or []) if f and f.filename]
    cat = (merchant_cat or "").strip()
    if cat:
        if cat not in MERCHANT_CAT_KEYS:
            raise HTTPException(400, "分类不存在")
    else:
        cat = None
    need_t = post_tier(board)
    tier_msg = "开通高级会员（¥{t2}）后才能在旅韩交友发帖" if need_t >= 2 else "开通会员（¥{t1}）后即可发帖"
    if len(images) > MAX_IMAGES:
        raise HTTPException(400, f"最多 {MAX_IMAGES} 张图片")
    act = (None, None, None)
    with db() as conn:     # 先确认身份再处理图片，未登录者不能消耗服务器算力
        u0 = need_tier(conn, req, need_t, tier_msg)
        if cat and not u0["merchant"]:
            raise HTTPException(400, "开通商家后才能选择商家分类")
        if board in MERCHANT_BOARDS or cat:
            from merch import clean_activity, MEDICAL_BOARDS
            from contact_guard import guard_text, guard_medical
            if u0["merchant"]:
                if board in MERCHANT_BOARDS:
                    act = clean_activity(board, tag, offer, valid_until)
                guard_text(conn, u0["id"], "商家活动帖", title, body, (act[1] if act else None) or "")
                if board in MEDICAL_BOARDS or cat == "clinic":
                    guard_medical(title + "\n" + body + "\n" + ((act[1] if act else None) or ""))
    processed, raws = [], []
    for f in images:
        raw = await f.read(MAX_IMG_BYTES + 1)
        processed.append(process_image(raw)); raws.append(raw)
    if board in MERCHANT_BOARDS and raws:
        from contact_guard import guard_images
        with db() as conn:
            guard_images(conn, u0["id"], "商家活动帖图片", raws)
    with db() as conn:
        u = need_tier(conn, req, need_t, tier_msg)
        if daily:
            nd = q(conn, """SELECT count(*) AS n FROM c_posts WHERE user_id=%s AND board=%s AND NOT deleted
                            AND (timezone('Asia/Seoul', created_at))::date = (timezone('Asia/Seoul', now()))::date""", (u["id"], DAILY_BOARD), one=True)["n"]
            if nd >= DAILY_CAP:
                raise HTTPException(429, f"「在韩日常」每天最多发 {DAILY_CAP} 篇，明天再来记录吧")
        st = get_settings(conn)
        status, quota_kind, note = "published", None, None
        listing = board in MERCHANT_BOARDS or bool(cat)
        if listing:
            if not u["merchant"]:
                raise HTTPException(403, f"{BOARDS.get(board, '该频道')}仅限商家会员发布（月付 ¥{st['price_m_month']} / 年付 ¥{st['price_m_year']}），详见会员页")
            mq = merchant_quota(conn, u)
            if mq["left"] > 0:
                quota_kind = "quota"
            elif u["merchant_credits"] > 0:
                quota_kind = "credit"
                q(conn, "UPDATE c_users SET merchant_credits=merchant_credits-1 WHERE id=%s AND merchant_credits>0", (u["id"],))
            else:
                raise HTTPException(403, f"本周期 {MERCHANT_QUOTA} 篇额度已用完（{mq['cycle_end']} 重置）。每增发 1 篇 ¥{st['price_extra']}，付款后联系管理员增加次数")
            status, note = "pending", "商家帖，先审后发"
        elif u["merchant"]:
            status, note = "pending", "商家帖，先审后发"
        elif not u["merchant"]:
            hits = is_commercial(title + "\n" + body)
            if hits:
                status = "pending"
                note = "含商业信息关键词：" + "、".join(hits[:5])
        limit("post:" + str(u["id"]), 3, 600, "发帖太频繁，请十分钟后再发")
        n = q(conn, "SELECT count(*) AS n FROM c_posts WHERE user_id=%s AND created_at>now()-interval '1 day'",
              (u["id"],), one=True)["n"]
        if n >= 20:
            raise HTTPException(429, "今天发帖已达上限（20 篇）")
        row = q(conn, """INSERT INTO c_posts(user_id,board,title,body,status,is_merchant,quota_kind,review_note,tag,offer,valid_until,merchant_cat)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (u["id"], board, title, body, status, bool(u["merchant"]), quota_kind, note, *act, cat), one=True)
        for i, (big, th, w, h) in enumerate(processed):
            q(conn, "INSERT INTO c_images(post_id,pos,data,thumb,width,height) VALUES (%s,%s,%s,%s,%s,%s)",
              (row["id"], i, big, th, w, h))
    if status == "pending" and (board in MERCHANT_BOARDS or cat):
        return {"ok": True, "id": row["id"], "pending": True,
                "message": "活动已提交，管理员审核通过后公开（一般 1 个工作日内）。审核期间可在「我的帖子」查看状态。"}
    if status == "pending":
        return {"ok": True, "id": row["id"], "pending": True,
                "message": "你的帖子含有商业推广类内容（如酒店预订、租车包车、医美、联系方式、代办等），已提交管理员审核，审核通过后才会公开。"
                           "住宿、包车用车、皮肤管理·医美、留学机构、代购、地陪、追星活动等商业信息请开通商家会员后在商家专区发布。"}
    with db() as conn:
        from retention import on_post_published
        on_post_published(conn, u["id"], row["id"], board)
    schedule_ai_reply("post", row["id"], u["id"])
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
        r = q(conn, f"""SELECT i.{col} AS d, p.board, p.status, p.user_id FROM c_images i JOIN c_posts p ON p.id=i.post_id
                        JOIN c_users au ON au.id=p.user_id
                        WHERE i.id=%s AND NOT p.deleted AND NOT au.is_banned""", (iid,), one=True)
        if not r:
            raise HTTPException(404, "图片不存在")
        if r["status"] != "published":
            cu = current_user(conn, req)
            if not cu or cu["id"] != r["user_id"]:
                raise HTTPException(404, "图片不存在")
            return Response(bytes(r["d"]), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
        if r["board"] in LOGIN_ONLY_BOARDS:
            cu = current_user(conn, req)
            if not cu or cu["tier"] < 1:
                raise HTTPException(403, "开通会员后查看")
    cache = "private, max-age=86400" if r["board"] in LOGIN_ONLY_BOARDS else "public, max-age=604800"
    return Response(bytes(r["d"]), media_type="image/jpeg", headers={"Cache-Control": cache})

@router.post("/api/community/posts/{pid}/comments")
async def api_comment(req: Request, pid: int):
    csrf(req)
    d = await _json(req)
    body = clean_text(d.get("body"), 500, "评论")
    with db() as conn:
        u = need_tier(conn, req, 1, "开通会员（¥{t1}）后才能评论")
        p = q(conn, "SELECT id,board,user_id,title FROM c_posts WHERE id=%s AND NOT deleted AND status='published'", (pid,), one=True)
        if not p:
            raise HTTPException(404, "帖子不存在或已删除")
        if p["board"] in LOGIN_ONLY_BOARDS and u["tier"] < 2:
            raise HTTPException(403, "旅韩交友频道评论需开通高级会员（¥%s）" % price(conn, "price_t2"))
        if u.get("id") and is_bot_uid(conn, u["id"]):
            raise HTTPException(403, "系统账号不能发言")
        limit("cm:" + str(u["id"]), 10, 300, "评论太频繁，请稍后再试")
        r = q(conn, "INSERT INTO c_comments(post_id,user_id,body) VALUES (%s,%s,%s) RETURNING id", (pid, u["id"], body), one=True)
        q(conn, "UPDATE c_posts SET comment_count=(SELECT count(*) FROM c_comments WHERE post_id=%s AND NOT deleted) WHERE id=%s", (pid, pid))
        from retention import on_comment
        on_comment(conn, u, p, r["id"], body)
        want_ai = comment_wants_ai(body, u["id"], p["user_id"])
    if want_ai:
        schedule_ai_reply("comment", pid, u["id"], body)
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
        u = need_tier(conn, req, 1, "开通会员（¥{t1}）后才能点赞")
        limit("like:" + str(u["id"]), 60, 600)
        if not q(conn, "SELECT id FROM c_posts WHERE id=%s AND NOT deleted AND status='published'", (pid,), one=True):
            raise HTTPException(404, "帖子不存在")
        had = q(conn, "DELETE FROM c_likes WHERE post_id=%s AND user_id=%s RETURNING post_id", (pid, u["id"]), one=True)
        if not had:
            q(conn, "INSERT INTO c_likes(post_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (pid, u["id"]))
            from retention import on_like
            on_like(conn, u, pid)
        r = q(conn, "UPDATE c_posts SET like_count=(SELECT count(*) FROM c_likes WHERE post_id=%s) WHERE id=%s RETURNING like_count",
              (pid, pid), one=True)
    return {"liked": not had, "likes": r["like_count"]}

@router.post("/api/community/report")
async def api_report(req: Request):
    csrf(req)
    d = await _json(req)
    tt = d.get("type")
    if tt not in ("post", "comment", "profile", "message", "user", "fan_review"):
        raise HTTPException(400, "举报对象无效")
    try:
        tid = int(d.get("id"))
    except Exception:
        raise HTTPException(400, "举报对象无效")
    reason = clean_text(d.get("reason"), 200, "举报理由")
    with db() as conn:
        # 私信和用户举报：登录即可（方便处理骚扰）。帖子/评论/交友资料仍需会员。
        if tt in ("message", "user", "fan_review"):
            u = need_user(conn, req)
        else:
            u = need_tier(conn, req, 1, "开通会员（¥{t1}）后才能举报；遇到严重违规也可直接联系管理员")
        limit("rep:" + str(u["id"]), 10, 3600, "举报太频繁，请稍后再试")
        if tt == "message" and not q(conn, "SELECT 1 AS x FROM c_messages WHERE id=%s AND (sender_id=%s OR recipient_id=%s)",
                                     (tid, u["id"], u["id"]), one=True):
            raise HTTPException(404, "消息不存在")
        if tt == "profile" and not q(conn, "SELECT 1 AS x FROM c_dating_profiles WHERE user_id=%s", (tid,), one=True):
            raise HTTPException(404, "资料不存在")
        if tt == "user":
            if tid == u["id"]:
                raise HTTPException(400, "不能举报自己")
            peer = q(conn, "SELECT id, COALESCE(is_bot,FALSE) AS is_bot FROM c_users WHERE id=%s AND NOT is_banned", (tid,), one=True)
            if not peer or peer["is_bot"]:
                raise HTTPException(404, "用户不存在")
        if tt == "fan_review":
            row = q(conn, "SELECT id, user_id FROM c_fan_place_ratings WHERE id=%s AND NOT hidden", (tid,), one=True)
            if not row:
                raise HTTPException(404, "评价不存在")
            if row["user_id"] == u["id"]:
                raise HTTPException(400, "不能举报自己的评价")
        q(conn, "INSERT INTO c_reports(target_type,target_id,user_id,reason) VALUES (%s,%s,%s,%s)", (tt, tid, u["id"], reason))
    return {"ok": True}

# ---------------- 管理员 API ----------------
ADMIN_FAIL_IP = (5, 900)        # 同一 IP 15 分钟内失败 5 次 → 锁 15 分钟
ADMIN_FAIL_GLOBAL = (20, 3600)  # 全站 1 小时内失败 20 次 → 全部锁 1 小时（防换 IP 爆破）
_adm_fail = defaultdict(deque)

def _adm_window(key, window, now):
    dq = _adm_fail[key]
    while dq and now - dq[0] > window:
        dq.popleft()
    return dq

def _admin_locked(ip):
    now = time.time()
    with _rl_lock:
        if len(_adm_window("ip:" + ip, ADMIN_FAIL_IP[1], now)) >= ADMIN_FAIL_IP[0]:
            raise HTTPException(429, "密码错误次数太多，请 15 分钟后再试")
        if len(_adm_window("global", ADMIN_FAIL_GLOBAL[1], now)) >= ADMIN_FAIL_GLOBAL[0]:
            raise HTTPException(429, "后台登录已临时锁定，请 1 小时后再试")

def _admin_fail(ip):
    now = time.time()
    with _rl_lock:
        _adm_window("ip:" + ip, ADMIN_FAIL_IP[1], now).append(now)
        _adm_window("global", ADMIN_FAIL_GLOBAL[1], now).append(now)

def _admin_ok(ip):
    with _rl_lock:
        _adm_fail.pop("ip:" + ip, None)

async def _json(req: Request) -> dict:
    try:
        d = await req.json()
    except Exception:
        raise HTTPException(400, "请求格式不正确")
    if not isinstance(d, dict):
        raise HTTPException(400, "请求格式不正确")
    return d

@router.post("/api/admin/login")
async def api_admin_login(req: Request, resp: Response):
    csrf(req)
    ip = ip_of(req)
    limit("adm:" + ip, 10, 900, "尝试太多，请 15 分钟后再试")          # 每 IP 总尝试
    _admin_locked(ip)                                                  # 失败次数锁定
    expected = os.environ.get("ADMIN_PASSWORD", "")
    if len(expected) < 12:
        raise HTTPException(503, "管理员后台暂未启用")
    d = await _json(req)
    given = str(d.get("password") or "")[:256].encode()
    if not hmac.compare_digest(hashlib.sha256(given).digest(), hashlib.sha256(expected.encode()).digest()):
        _admin_fail(ip)
        await asyncio.sleep(0.5)  # 拖慢逐个试密码（不阻塞其他请求）
        print("admin login failed", flush=True)   # 只记录事件，不记录密码或 IP
        raise HTTPException(401, "密码不对")
    _admin_ok(ip)
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
                              (SELECT count(*) FROM c_users WHERE tier=1) AS members,
                              (SELECT count(*) FROM c_users WHERE tier=2) AS premium,
                              (SELECT count(*) FROM c_users WHERE merchant_until>now()) AS merchants,
                              (SELECT count(*) FROM c_posts WHERE status='pending' AND NOT deleted) AS pending,
                              (SELECT count(*) FROM c_posts WHERE NOT deleted) AS posts,
                              (SELECT count(*) FROM c_comments WHERE NOT deleted) AS comments,
                              (SELECT count(*) FROM c_reports WHERE NOT resolved) AS reports,
                              (SELECT count(*) FROM c_dating_profiles) AS profiles,
                              (SELECT count(*) FROM c_messages WHERE flagged AND created_at>now()-interval '7 days') AS flagged7,
                              (SELECT count(*) FROM c_shop_recs WHERE status='pending') AS shop_pending,
                              (SELECT count(*) FROM c_shop_reviews WHERE status='pending') AS review_pending,
                              pg_database_size(current_database()) AS db_bytes""", one=True)
        s["db_mb"] = round(s.pop("db_bytes") / 1048576, 1)
        try:
            from visits import overview_visits
            s["visits"] = overview_visits(conn)
        except Exception:
            s["visits"] = {"today": {"day": None, "pv": 0, "uv": 0}, "days": [], "sources": [], "today_sources": []}
    return s


@router.get("/api/admin/tab-counts")
def api_admin_tab_counts(req: Request):
    """后台标签栏待处理数量。仅管理员。键名与按钮 data-t 一致。"""
    keys = ("payproofs", "reports", "review", "merchants", "chicken", "promo",
            "friends", "idv", "consult", "providers", "referral", "feedback")
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, f"""SELECT
            (SELECT count(*) FROM c_pay_proofs WHERE status='pending') AS payproofs,
            (SELECT count(*) FROM c_reports WHERE NOT resolved) AS reports,
            (SELECT count(*) FROM c_posts WHERE {REVIEW_POSTS_COND})
          + (SELECT count(*) FROM c_shop_recs WHERE {REVIEW_SHOP_RECS_COND})
          + (SELECT count(*) FROM c_fan_place_subs WHERE {REVIEW_FANPLACES_COND}) AS review,
            (SELECT count(*) FROM c_shop_reviews WHERE status='pending') AS merchants,
            (SELECT count(*) FROM c_chicken_orders WHERE status='pending_review')
          + (SELECT count(*) FROM c_chicken_merchants WHERE status='pending') AS chicken,
            (SELECT count(*) FROM c_promo_applications WHERE status='pending') AS promo,
            (SELECT count(*) FROM c_dating_profiles
              WHERE auto_hidden AND NOT COALESCE(hidden_by_admin, FALSE)) AS friends,
            (SELECT count(*) FROM c_id_verifications WHERE status='pending') AS idv,
            (SELECT count(*) FROM c_consult_threads
              WHERE merchant_id IS NULL AND merch_unread > 0) AS consult,
            (SELECT count(*) FROM c_providers WHERE status='pending') AS providers,
            (SELECT count(*) FROM c_ref_invites WHERE status='frozen') AS referral,
            (SELECT count(*) FROM c_feedback WHERE status='new') AS feedback""", one=True)
    return {k: int(row[k] or 0) for k in keys}


@router.get("/api/admin/users")
def api_admin_users(req: Request, s: str = ""):
    with db() as conn:
        need_admin(conn, req)
        s = s.strip()[:60]
        if s:
            like = "%" + s.replace("%", "").replace("_", "") + "%"
            # 纯数字按对外会员编号精确查找，避免命中邮箱/手机号里的数字片段
            if s.isdigit() and len(s) < 18:
                rows = q(conn, """SELECT id,phone,email,nickname,tier,is_banned,created_at,promoter_code,member_since,tier2_since,
                                         merchant_until,merchant_plan,merchant_credits,last_login,verified,is_bot,member_no,
                                         (avatar IS NOT NULL OR COALESCE(is_bot, FALSE)) AS has_avatar
                                  FROM c_users WHERE member_no=%s
                                  ORDER BY id DESC LIMIT 50""", (int(s),))
            else:
                rows = q(conn, """SELECT id,phone,email,nickname,tier,is_banned,created_at,promoter_code,member_since,tier2_since,
                                         merchant_until,merchant_plan,merchant_credits,last_login,verified,is_bot,member_no,
                                         (avatar IS NOT NULL OR COALESCE(is_bot, FALSE)) AS has_avatar
                                  FROM c_users WHERE nickname ILIKE %s OR email ILIKE %s OR phone ILIKE %s
                                  ORDER BY id DESC LIMIT 50""", (like, like, like))
        else:
            rows = q(conn, """SELECT id,phone,email,nickname,tier,is_banned,created_at,promoter_code,member_since,tier2_since,
                                     merchant_until,merchant_plan,merchant_credits,last_login,verified,is_bot,member_no,
                                     (avatar IS NOT NULL OR COALESCE(is_bot, FALSE)) AS has_avatar
                              FROM c_users ORDER BY id DESC LIMIT 50""")
    return {"users": [{"id": r["id"], "member_no": r.get("member_no") or r["id"], "nickname": r["nickname"], "account": r["email"] or r["phone"],
                       "tier": r["tier"], "is_banned": r["is_banned"], "joined": _ts(r["created_at"]),
                       "promoter": r.get("promoter_code"),
                       "member_since": _ts(r["member_since"]), "tier2_since": _ts(r["tier2_since"]),
                       "merchant_until": _ts(r["merchant_until"]), "merchant_plan": r["merchant_plan"],
                       "merchant_active": bool(r["merchant_until"] and r["merchant_until"] > datetime.datetime.now(datetime.timezone.utc)),
                       "merchant_credits": r["merchant_credits"], "last_login": _ts(r["last_login"]),
                       "verified": bool(r.get("verified")), "is_bot": bool(r.get("is_bot")),
                       "has_avatar": has_avatar_row(r)} for r in rows]}

@router.post("/api/admin/users/{uid}")
async def api_admin_user_action(req: Request, uid: int):
    csrf(req)
    d = await _json(req)
    act = d.get("action")
    with db() as conn:
        need_admin(conn, req)
        if not q(conn, "SELECT id FROM c_users WHERE id=%s", (uid,), one=True):
            raise HTTPException(404, "用户不存在")
        if is_bot_uid(conn, uid):
            raise HTTPException(403, "系统账号不可修改")
        if act == "set_tier":
            try:
                t = int(d.get("tier"))
            except Exception:
                t = -1
            if t not in (0, 1, 2):
                raise HTTPException(400, "等级只能是 0/1/2")
            set_tier(conn, uid, t)
        elif act == "confirm_payment":
            from promo import confirm_payment
            apply = str(d.get("apply_benefit", True)).lower() not in ("0", "false", "no")
            return confirm_payment(conn, uid, d.get("kind"), d.get("amount"), d.get("note") or "", apply_benefit=apply)
        elif act == "merchant":
            plan = d.get("plan")
            if plan not in PLAN_DAYS:
                raise HTTPException(400, "请选择月付或年付")
            return {"ok": True, **merchant_activate(conn, uid, plan)}
        elif act == "merchant_off":
            q(conn, "UPDATE c_users SET merchant_until=now() WHERE id=%s AND merchant_until>now()", (uid,))
            q(conn, "INSERT INTO c_merchant_log(user_id,action) VALUES (%s,'off')", (uid,))
        elif act == "credits":
            try:
                n = int(d.get("n"))
            except Exception:
                n = 0
            if n == 0 or abs(n) > 100:
                raise HTTPException(400, "次数应为 1–100（负数为扣减）")
            r = q(conn, "UPDATE c_users SET merchant_credits=GREATEST(0, merchant_credits+%s) WHERE id=%s RETURNING merchant_credits", (n, uid), one=True)
            q(conn, "INSERT INTO c_merchant_log(user_id,action,detail) VALUES (%s,'credits',%s)", (uid, str(n)))
            return {"ok": True, "credits": r["merchant_credits"]}
        elif act == "ban":
            q(conn, "UPDATE c_users SET is_banned=TRUE WHERE id=%s", (uid,))
            q(conn, "DELETE FROM c_sessions WHERE user_id=%s", (uid,))
        elif act == "unban":
            q(conn, "UPDATE c_users SET is_banned=FALSE WHERE id=%s", (uid,))
        elif act in ("verify", "unverify"):     # “已认证”标识：管理员人工核实后手动打开
            q(conn, "UPDATE c_users SET verified=%s WHERE id=%s", (act == "verify", uid))
        elif act == "purge":
            q(conn, "UPDATE c_posts SET deleted=TRUE WHERE user_id=%s", (uid,))
            pids = q(conn, "UPDATE c_comments SET deleted=TRUE WHERE user_id=%s RETURNING post_id", (uid,))
            for pid in {p["post_id"] for p in pids}:
                _recount(conn, pid)
            q(conn, "DELETE FROM c_dating_photos WHERE user_id=%s", (uid,))
            q(conn, "DELETE FROM c_dating_profiles WHERE user_id=%s", (uid,))
            q(conn, "UPDATE c_messages SET deleted=TRUE WHERE sender_id=%s", (uid,))
        elif act == "delete_account":   # 处理用户的删除个人信息请求：彻底删除账号及全部内容
            delete_user_data(conn, uid)
            print("admin deleted account", flush=True)
            return {"ok": True, "deleted": True}
        elif act == "reset_pw":
            tmp = secrets.token_urlsafe(8)
            q(conn, "UPDATE c_users SET pw_hash=%s WHERE id=%s", (hash_pw(tmp), uid))
            q(conn, "DELETE FROM c_sessions WHERE user_id=%s", (uid,))
            return {"ok": True, "temp_password": tmp}
        else:
            raise HTTPException(400, "未知操作")
    return {"ok": True}

@router.post("/api/admin/users/{uid}/member_no")
async def api_admin_set_member_no(req: Request, uid: int):
    csrf(req)
    d = await _json(req)
    try:
        n = int(d.get("member_no"))
    except Exception:
        n = 0
    if n < 1:
        raise HTTPException(400, "编号须为正整数")
    with db() as conn:
        need_admin(conn, req)
        if not q(conn, "SELECT id FROM c_users WHERE id=%s", (uid,), one=True):
            raise HTTPException(404, "用户不存在")
        if is_bot_uid(conn, uid):
            raise HTTPException(403, "系统账号不可修改")
        cur = q(conn, "SELECT member_no FROM c_users WHERE id=%s", (uid,), one=True)
        if cur and cur["member_no"] == n:
            return {"ok": True, "member_no": n}
        taken = q(conn, "SELECT id FROM c_users WHERE member_no=%s AND id<>%s", (n, uid), one=True)
        if taken:
            raise HTTPException(409, "这个编号已被占用")
        q(conn, "UPDATE c_users SET member_no=%s WHERE id=%s", (n, uid))
    return {"ok": True, "member_no": n}

@router.post("/api/admin/users/{uid}/clear_avatar")
def api_admin_clear_avatar(req: Request, uid: int):
    csrf(req)
    with db() as conn:
        need_admin(conn, req)
        if not q(conn, "SELECT id FROM c_users WHERE id=%s", (uid,), one=True):
            raise HTTPException(404, "用户不存在")
        if is_bot_uid(conn, uid):
            raise HTTPException(403, "系统账号不可修改")
        q(conn, "UPDATE c_users SET avatar=NULL WHERE id=%s", (uid,))
    return {"ok": True}

@router.get("/api/admin/posts")
def api_admin_posts(req: Request, before: int = 0):
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT p.*, au.nickname, au.member_no, (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier FROM c_posts p JOIN c_users au ON au.id=p.user_id
                          WHERE NOT p.deleted AND p.status<>'rejected' AND (%s=0 OR p.id<%s) ORDER BY p.id DESC LIMIT 30""", (before, before))
    out = []
    for r in rows:
        o = _post_out(r); o["user_id"] = r["user_id"]; o["member_no"] = r.get("member_no") or r["user_id"]; out.append(o)
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
                 WHEN r.target_type='comment' THEN (SELECT body FROM c_comments WHERE id=r.target_id)
                 WHEN r.target_type='message' THEN (SELECT body FROM c_messages WHERE id=r.target_id)
                 WHEN r.target_type='user' THEN (SELECT nickname FROM c_users WHERE id=r.target_id)
                 WHEN r.target_type='fan_review' THEN (
                   SELECT COALESCE(p.name,'地点') || ' · ' || COALESCE(u2.nickname,'') || '：' || COALESCE(fr.body,'')
                   FROM c_fan_place_ratings fr
                   JOIN c_fan_places p ON p.id=fr.place_id
                   JOIN c_users u2 ON u2.id=fr.user_id
                   WHERE fr.id=r.target_id)
                 ELSE (SELECT nickname || '：' || intro FROM c_dating_profiles WHERE user_id=r.target_id) END AS target_text,
            CASE WHEN r.target_type='post' THEN r.target_id
                 WHEN r.target_type='comment' THEN (SELECT post_id FROM c_comments WHERE id=r.target_id) END AS post_id,
            CASE WHEN r.target_type='post' THEN (SELECT user_id FROM c_posts WHERE id=r.target_id)
                 WHEN r.target_type='comment' THEN (SELECT user_id FROM c_comments WHERE id=r.target_id)
                 WHEN r.target_type='message' THEN (SELECT sender_id FROM c_messages WHERE id=r.target_id)
                 WHEN r.target_type='user' THEN r.target_id
                 WHEN r.target_type='fan_review' THEN (SELECT user_id FROM c_fan_place_ratings WHERE id=r.target_id)
                 ELSE r.target_id END AS target_user,
            CASE WHEN r.target_type='fan_review' THEN (SELECT place_id FROM c_fan_place_ratings WHERE id=r.target_id) END AS place_id
            FROM c_reports r LEFT JOIN c_users u ON u.id=r.user_id
            WHERE NOT r.resolved ORDER BY r.id DESC LIMIT 100""")
    return {"reports": [{"id": r["id"], "type": r["target_type"], "target_id": r["target_id"], "post_id": r["post_id"],
                         "place_id": r.get("place_id"),
                         "target_user": r["target_user"], "text": (r["target_text"] or "")[:120], "reason": r["reason"],
                         "reporter": r["reporter"] or ("系统自动标记" if r["user_id"] is None else None),
                         "time": _ts(r["created_at"])} for r in rows]}

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
        rows = q(conn, """SELECT c.id,c.body,c.created_at,c.user_id,au.nickname,au.is_bot FROM c_comments c
                          JOIN c_users au ON au.id=c.user_id WHERE c.post_id=%s AND NOT c.deleted ORDER BY c.id""", (pid,))
    return {"comments": [{"id": r["id"], "body": r["body"], "author": r["nickname"], "user_id": r["user_id"],
                          "time": _ts(r["created_at"]), "is_bot": bool(r.get("is_bot"))} for r in rows]}

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
            day = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date()
            # 带密钥的哈希（密钥由服务器配置派生、按天变化）：不保存原始 IP，也无法靠穷举 IP 反推
            vkey = hashlib.sha256(("lh-ref-visitor|" + _db_url() + "|" + day.isoformat()).encode()).digest()
            visitor = hmac.new(vkey, (ip_of(request) + "|" + (request.headers.get("user-agent") or "")[:200]).encode(),
                               hashlib.sha256).hexdigest()[:32]
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
            (SELECT count(*) FROM c_commissions c WHERE c.code=p.code AND c.tier=1 AND COALESCE(c.status,'payable')<>'void' AND c.created_at>=%(a)s AND c.created_at<%(b)s) AS paid1,
            (SELECT count(*) FROM c_commissions c WHERE c.code=p.code AND c.tier=2 AND COALESCE(c.status,'payable')<>'void' AND c.created_at>=%(a)s AND c.created_at<%(b)s) AS paid2,
            (SELECT count(*) FROM c_commissions c WHERE c.code=p.code AND c.tier=3 AND COALESCE(c.status,'payable')<>'void' AND c.created_at>=%(a)s AND c.created_at<%(b)s) AS paid3,
            (SELECT count(*) FROM c_commissions c WHERE c.code=p.code AND c.tier=4 AND COALESCE(c.status,'payable')<>'void' AND c.created_at>=%(a)s AND c.created_at<%(b)s) AS paid4,
            (SELECT COALESCE(sum(cents),0) FROM c_commissions c WHERE c.code=p.code AND COALESCE(c.status,'payable')<>'void' AND c.created_at>=%(a)s AND c.created_at<%(b)s) AS range_cents,
            (SELECT count(*) FROM c_commissions c WHERE c.code=p.code AND c.settled_at IS NULL AND COALESCE(c.status,'payable')<>'void') AS unsettled,
            (SELECT COALESCE(sum(cents),0) FROM c_commissions c WHERE c.code=p.code AND c.settled_at IS NULL AND COALESCE(c.status,'payable')<>'void') AS owed_cents,
            (SELECT COALESCE(sum(amount_cents),0) FROM c_settlements s WHERE s.code=p.code) AS settled_cents
            FROM c_promoters p ORDER BY p.created_at""", {"a": t0, "b": t1})
    return {"promoters": [{"code": r["code"], "name": r["name"], "active": r["active"],
                           "commission": r["commission_cents"] / 100, "commission2": r["commission2_cents"] / 100,
                           "visits": r["visits"], "regs": r["regs"],
                           "commission3": r["commission3_cents"] / 100, "commission4": r["commission4_cents"] / 100,
                           "paid1": r["paid1"], "paid2": r["paid2"], "paid3": r["paid3"], "paid4": r["paid4"],
                           "paid": r["paid1"] + r["paid2"] + r["paid3"] + r["paid4"],
                           "commission_in_range": r["range_cents"] / 100,
                           "unsettled_members": r["unsettled"], "owed": r["owed_cents"] / 100,
                           "settled_total": r["settled_cents"] / 100,
                           "douyin_id": r.get("douyin_id") or "",
                           "promo_method": r.get("promo_method") or ""} for r in rows]}

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
    d = await _json(req)
    code = norm_code(d.get("code"))
    if not code:
        raise HTTPException(400, "推广码只能是 2–12 位字母或数字，例如 A08")
    name = clean_text(d.get("name"), 30, "推广员名称")
    cents = _cents(d.get("commission", 3))
    cents2 = _cents(d.get("commission2", 6))
    cents3 = _cents(d.get("commission3", 20))
    cents4 = _cents(d.get("commission4", 200))
    with db() as conn:
        need_admin(conn, req)
        if q(conn, "SELECT code FROM c_promoters WHERE code=%s", (code,), one=True):
            raise HTTPException(409, "推广码已存在")
        q(conn, "INSERT INTO c_promoters(code,name,commission_cents,commission2_cents,commission3_cents,commission4_cents) VALUES (%s,%s,%s,%s,%s,%s)",
          (code, name, cents, cents2, cents3, cents4))
        active_codes(conn)
    return {"ok": True, "code": code}

@router.post("/api/admin/promoters/{code}")
async def api_admin_promoter_action(req: Request, code: str):
    csrf(req)
    d = await _json(req)
    code = norm_code(code)
    act = d.get("action")
    with db() as conn:
        need_admin(conn, req)
        p = q(conn, "SELECT * FROM c_promoters WHERE code=%s", (code,), one=True)
        if not p:
            raise HTTPException(404, "推广员不存在")
        if act == "commission":
            c1 = _cents(d.get("commission")) if d.get("commission") not in (None, "") else p["commission_cents"]
            c2 = _cents(d.get("commission2")) if d.get("commission2") not in (None, "") else p["commission2_cents"]
            c3 = _cents(d.get("commission3")) if d.get("commission3") not in (None, "") else p["commission3_cents"]
            c4 = _cents(d.get("commission4")) if d.get("commission4") not in (None, "") else p["commission4_cents"]
            q(conn, "UPDATE c_promoters SET commission_cents=%s, commission2_cents=%s, commission3_cents=%s, commission4_cents=%s WHERE code=%s",
              (c1, c2, c3, c4, code))
        elif act in ("disable", "enable"):
            q(conn, "UPDATE c_promoters SET active=%s WHERE code=%s", (act == "enable", code))
        elif act == "settle":
            from promo import refresh_status
            for row in q(conn, "SELECT * FROM c_commissions WHERE code=%s AND status='pending'", (code,)):
                refresh_status(conn, row)
            rows = q(conn, """UPDATE c_commissions SET settled_at=now(), status='paid', paid_at=now(),
                              paid_note=COALESCE(paid_note, '批量结算')
                              WHERE code=%s AND settled_at IS NULL AND status='payable'
                              RETURNING cents""", (code,))
            n = len(rows)
            amt = sum(r["cents"] for r in rows)
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


def set_tier(conn, uid, t):
    """设置会员等级。推广佣金改为按管理员确认的实付金额 50% 记账，见 promo.confirm_payment。"""
    q(conn, """UPDATE c_users SET tier=%s, is_member=%s,
               member_since=CASE WHEN %s>=1 THEN COALESCE(member_since, now()) ELSE member_since END,
               tier2_since=CASE WHEN %s>=2 THEN COALESCE(tier2_since, now()) ELSE tier2_since END
               WHERE id=%s""", (t, t >= 1, t, t, uid))


def merchant_activate(conn, uid, plan):
    """开通/续费商家会员：有效期内续费顺延，过期后重新开通从现在起算。佣金由 confirm_payment 按实付记录。"""
    days = PLAN_DAYS[plan]
    r = q(conn, """UPDATE c_users SET
            merchant_started=CASE WHEN merchant_until IS NOT NULL AND merchant_until>now() THEN merchant_started ELSE now() END,
            merchant_until=GREATEST(COALESCE(merchant_until, now()), now()) + make_interval(days => %s),
            merchant_plan=%s
            WHERE id=%s RETURNING merchant_until, promoter_code""", (days, plan, uid), one=True)
    q(conn, "INSERT INTO c_merchant_log(user_id,action,detail) VALUES (%s,'activate',%s)", (uid, plan))
    return {"merchant_until": _ts(r["merchant_until"]), "plan": plan}

@router.get("/api/admin/pending")
def api_admin_pending(req: Request):
    """待审核帖子列表：含商家待审帖。"""
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, f"""SELECT p.*, au.nickname, au.member_no,
                                  (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier
                           FROM c_posts p JOIN c_users au ON au.id=p.user_id
                           WHERE {PENDING_POSTS_COND}
                           ORDER BY p.id LIMIT 100""")
        imgs = {}
        if rows:
            for i in q(conn, "SELECT id,post_id FROM c_images WHERE post_id = ANY(%s) ORDER BY pos",
                       ([r["id"] for r in rows],)):
                imgs.setdefault(i["post_id"], []).append(i["id"])
    out = []
    for r in rows:
        o = _post_out(r, full=True)
        # 后台审核卡片需要的字段（_post_out 已带 title/author/board/board_name/time/status/merchant）
        o["user_id"] = r["user_id"]
        o["member_no"] = r.get("member_no") or r["user_id"]
        o["note"] = r["review_note"]
        o["images"] = imgs.get(r["id"], [])
        o["merchant"] = bool(r.get("is_merchant"))   # 前端据此显示「商家帖」标记
        out.append(o)
    return {"posts": out}


@router.get("/api/admin/img/{iid}")
def api_admin_img(req: Request, iid: int):
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT thumb FROM c_images WHERE id=%s", (iid,), one=True)
    if not r:
        raise HTTPException(404, "图片不存在")
    return Response(bytes(r["thumb"]), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})

@router.post("/api/admin/posts/{pid}/review")
async def api_admin_review(req: Request, pid: int):
    csrf(req)
    d = await _json(req)
    act = d.get("action")
    if act not in ("approve", "reject"):
        raise HTTPException(400, "未知操作")
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "UPDATE c_posts SET status=%s, review_note=%s WHERE id=%s AND status='pending' RETURNING id,user_id",
              ("published" if act == "approve" else "rejected",
               None if act == "approve" else (clean_text(d.get("reason") or "不符合社区约定", 100, "理由")), pid), one=True)
        if not r:
            raise HTTPException(404, "该帖子不在待审核队列")
        uid = r["user_id"]
        if act == "approve":
            from retention import on_post_published
            on_post_published(conn, uid, pid, "")
    if act == "approve":
        schedule_ai_reply("post", pid, uid)
    return {"ok": True}

@router.get("/api/admin/settings")
def api_admin_settings(req: Request):
    with db() as conn:
        need_admin(conn, req)
        st = get_settings(conn)
    return {"settings": [{"key": k, "label": SETTING_LABELS[k], "value": v,
                          "type": "bool" if k in BOOL_SETTINGS else "number"} for k, v in st.items()]}

@router.post("/api/admin/settings")
async def api_admin_settings_save(req: Request):
    csrf(req)
    d = await _json(req)
    vals = {}
    for k in DEFAULT_SETTINGS:
        if k in d:
            v = str(d[k]).strip()
            if k in BOOL_SETTINGS:
                vals[k] = "1" if v.lower() in ("1", "true", "on", "yes", "开", "开启") else "0"
                continue
            try:
                f = float(v)
            except ValueError:
                raise HTTPException(400, f"{SETTING_LABELS[k]}需为数字")
            if f < 0 or f > 100000:
                raise HTTPException(400, f"{SETTING_LABELS[k]}超出范围")
            vals[k] = ("%.2f" % f).rstrip("0").rstrip(".")
    with db() as conn:
        need_admin(conn, req)
        for k, v in vals.items():
            q(conn, """INSERT INTO c_settings(key,value) VALUES (%s,%s)
                       ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""", (k, v))
        st = get_settings(conn)
    return {"ok": True, "settings": st}

# 交友/相亲（friends.py 依赖本模块的工具函数，放在文件末尾导入）
from friends import router as _friends_router   # noqa: E402
router.include_router(_friends_router)
from points import router as _points_router   # noqa: E402
router.include_router(_points_router)
from chicken import router as _chicken_router   # noqa: E402
router.include_router(_chicken_router)
from guides import router as _guides_router   # noqa: E402
router.include_router(_guides_router)
from shop_extras import router as _shop_extras_router   # noqa: E402
router.include_router(_shop_extras_router)
from buddy_verify import router as _buddy_verify_router   # noqa: E402
router.include_router(_buddy_verify_router)
from promo import router as _promo_router   # noqa: E402
router.include_router(_promo_router)
from opening_promo import router as _opening_promo_router   # noqa: E402
router.include_router(_opening_promo_router)
from pay_proof import router as _pay_proof_router   # noqa: E402
router.include_router(_pay_proof_router)
from chicken_merchant import router as _chicken_merchant_router   # noqa: E402
router.include_router(_chicken_merchant_router)
from providers import router as _providers_router   # noqa: E402
router.include_router(_providers_router)
from merch import router as _merch_router  # noqa: E402
router.include_router(_merch_router)
from retention import router as _retention_router  # noqa: E402
router.include_router(_retention_router)
from visits import router as _visits_router  # noqa: E402
router.include_router(_visits_router)
from me import router as _me_router  # noqa: E402
router.include_router(_me_router)
from chat import router as _chat_router  # noqa: E402
router.include_router(_chat_router)
from fanplaces import router as _fanplaces_router  # noqa: E402
router.include_router(_fanplaces_router)
