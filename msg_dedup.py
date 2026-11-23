"""发送防重（私信 / 站内咨询 / 评论）：
1) 客户端每条消息带一个 cid（随机串），同一用户同一 cid 在 10 分钟内只落库一次，重复请求直接返回第一次的结果；
2) 没带 cid 的旧客户端：同一会话同一人几秒内发完全相同的内容，视为连点，返回上一条。"""
import re, threading, time

_CID_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
_TTL = 600
_seen: dict = {}
_lock = threading.Lock()


def norm_cid(cid) -> str:
    c = str(cid or "").strip()
    return c if _CID_RE.match(c) else ""


def cid_get(kind: str, uid: int, cid: str):
    if not cid:
        return None
    now = time.time()
    with _lock:
        if len(_seen) > 5000:
            for k in [k for k, (t, _) in _seen.items() if now - t > _TTL]:
                _seen.pop(k, None)
        v = _seen.get((kind, uid, cid))
        if v and now - v[0] <= _TTL:
            return v[1]
    return None


def cid_put(kind: str, uid: int, cid: str, result) -> None:
    if not cid:
        return
    with _lock:
        _seen[(kind, uid, cid)] = (time.time(), result)
