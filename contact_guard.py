"""站外联系方式拦截：商家资料 / 活动帖 / 横幅 / 站内咨询消息里，不允许留微信、QQ、电话、二维码等站外联系方式。
命中后拒绝提交并记录到 c_contact_hits，管理员可在后台查看。"""
import io
import re
import unicodedata

from fastapi import HTTPException

MIGRATE_CONTACT_GUARD = """
CREATE TABLE IF NOT EXISTS c_contact_hits (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES c_users(id) ON DELETE CASCADE,
  place TEXT NOT NULL,
  kind TEXT NOT NULL,
  snippet TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_contact_hits_idx ON c_contact_hits(id DESC);
"""

# 关键词（统一转小写、全角转半角、去空格后匹配）
_WORDS = [
    "微信", "威信", "薇信", "徽信", "v信", "微x", "wechat", "weixin", "vx", "wx", "加v", "加微", "+v", "加我v", "扣扣", "qq",
    "企鹅号", "电话", "手机号", "联系电话", "打电话", "来电", "tel:", "whatsapp", "telegram", "kakaotalk", "kakao", "카톡", "카카오",
    "line id", "lineid", "小红书号", "抖音号", "私下联系", "站外联系", "扫码", "二维码", "加好友",
]
_WORD_RE = re.compile("|".join(re.escape(w.replace(" ", "")) for w in _WORDS))
# 电话：中国手机 1[3-9]xxxxxxxxx、韩国 010-xxxx-xxxx / +82、通用 9 位以上连续数字（允许空格/横线分隔）
_PHONE_RES = [
    re.compile(r"(?<!\d)(?:\+?86)?1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)(?:\+?82|0)1[016789]\d{7,8}(?!\d)"),
    re.compile(r"(?<!\d)0\d{1,2}\d{7,8}(?!\d)"),
    re.compile(r"(?<![\d.,])\d{9,}(?![\d.,])"),
]
# QQ 号：5-11 位数字紧跟 / 前缀 qq（关键词已覆盖 qq，这里兜底“Q:12345”）
_Q_RE = re.compile(r"(?:^|[^a-z])q[:：]?\d{5,11}")
# 疑似微信号：字母开头 6-20 位且混有数字/下划线（只在“号”“id”附近时判断，避免误伤）
_ID_RE = re.compile(r"(?:号|id|账号)[:：]?[a-z][a-z0-9_-]{5,19}")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\s\u200b\u200c\u200d\ufeff·•．。、,，\-_/|]+", "", s)


_DATE_RE = re.compile(r"\d{4}\s*[-./年]\s*\d{1,2}\s*[-./月]\s*\d{1,2}\s*日?|\d{1,2}\s*[:：]\s*\d{2}|\d{1,2}\s*[./月]\s*\d{1,2}\s*日?")


def _digits_norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = _DATE_RE.sub(" , ", s)
    s = s.translate(str.maketrans("零〇一二三四五六七八九", "00123456789"))
    s = s.translate(str.maketrans("壹贰叁肆伍陆柒捌玖", "123456789"))
    s = s.replace("o", "0") if re.search(r"\d[o]\d|\do\d", s) else s
    return re.sub(r"(?<=\d)[\s\-‐–—()（）·]+(?=\d)", "", s)


def find_contacts(text: str) -> list:
    """返回命中的类别列表（空列表 = 干净）"""
    if not text:
        return []
    hits = []
    n = _norm(text)
    m = _WORD_RE.search(n)
    if m:
        hits.append("关键词「%s」" % m.group(0))
    d = _digits_norm(text)
    for r in _PHONE_RES:
        if r.search(d):
            hits.append("电话号码")
            break
    if _Q_RE.search(n):
        hits.append("QQ号")
    if _ID_RE.search(n):
        hits.append("疑似账号")
    return hits


def image_has_qr(data: bytes) -> bool:
    """图片里是否有二维码/条码（zxing-cpp）。库不可用时返回 False（不阻塞业务）。"""
    try:
        import zxingcpp
        from PIL import Image
    except Exception:
        return False
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = im.convert("RGB")
        if max(im.size) > 1600:
            im.thumbnail((1600, 1600))
        if zxingcpp.read_barcodes(im):
            return True
        g = im.convert("L")
        return bool(zxingcpp.read_barcodes(g))
    except Exception:
        return False


def log_hit(conn, user_id, place, kind, snippet=""):
    from community import q
    try:
        q(conn, "INSERT INTO c_contact_hits(user_id,place,kind,snippet) VALUES (%s,%s,%s,%s)",
          (user_id, place[:40], kind[:80], (snippet or "")[:200]))
    except Exception:
        pass


MSG = "为保护双方权益，请勿留微信、QQ、电话、二维码等站外联系方式，所有咨询请通过站内「立即咨询」沟通。"


def guard_text(conn, user_id, place, *texts):
    """任一文本含联系方式 → 记录后抛 400（调用方需在可提交的事务外记录，故这里单独开连接写日志）"""
    for t in texts:
        h = find_contacts(t or "")
        if h:
            _log_separately(user_id, place, "、".join(h), t)
            raise HTTPException(400, "检测到站外联系方式（%s）。%s" % ("、".join(h), MSG))


def guard_images(conn, user_id, place, blobs):
    for b in blobs:
        if b and image_has_qr(b):
            _log_separately(user_id, place, "图片含二维码", "")
            raise HTTPException(400, "图片中检测到二维码。" + MSG)


def _log_separately(user_id, place, kind, snippet):
    """抛异常会回滚当前事务，所以用独立连接记录拦截日志"""
    try:
        from community import db
        with db() as c2:
            log_hit(c2, user_id, place, kind, snippet)
    except Exception:
        pass


# 医美类：不得承诺效果
EFFECT_WORDS = ["保证效果", "包效果", "百分百", "100%有效", "100%成功", "无副作用", "零风险", "无风险", "永久有效", "根治", "包满意", "无效退款", "立竿见影"]


def guard_medical(text: str):
    n = _norm(text)
    for w in EFFECT_WORDS:
        if _norm(w) in n:
            raise HTTPException(400, "医美/医疗信息不得承诺效果（检测到「%s」），请修改后再提交。" % w)
