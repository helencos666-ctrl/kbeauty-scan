"""首尔国际电影大奖（2026-12-17）入场名额：邀请 5 位好友注册旅韩助手即可申请。

不新建推广体系，只读已有的两套邀请记录：
  1) 邀请有奖（me.py：c_ref_invites，任何登录用户都有邀请码/链接，带同设备/同网络审核）
  2) 推广员（promo.py：c_promoters + c_users.promoter_code）
一个被邀请账号只算一次（按账号去重），排除自己、已封禁账号和系统账号。
「同网络批量注册 / 1 小时内多人」等已被邀请有奖标记为复核中的记录：照常计数，但在后台打标，
由管理员在确认名额时人工判断，这里不拦截；「同设备互邀」「人工判无效」不计入。
累计人数按全部时间统计（不限活动开始之后）。
名额最终以主办方确认为准：用户提交申请 → 后台 待确认 / 已确认 / 未通过。只增表，不改旧表。
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException, Request, Response

from community import _json, _page, _ts, clean_text, csrf, current_user, db, limit, need_admin, need_user, q

router = APIRouter()
KST = datetime.timezone(datetime.timedelta(hours=9))

EVENT_KEY = "seoul-global-movie-awards-2026"
EVENT_PATH = "/event/seoul-movie-awards"
NEED = 5
# 活动 2026-12-17 17:00（韩国时间）开始，申请到开始前截止
CLOSES_AT = datetime.datetime(2026, 12, 17, 17, 0, tzinfo=KST)
STATUS_LABEL = {"pending": "待主办方确认", "confirmed": "已确认", "declined": "未通过"}

MIGRATE_EVENT_GMA = """
CREATE TABLE IF NOT EXISTS c_event_claims (
  id BIGSERIAL PRIMARY KEY,
  event TEXT NOT NULL,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  referral_count INT NOT NULL DEFAULT 0,
  flagged_count INT NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  admin_note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ,
  UNIQUE (event, user_id)
);
CREATE INDEX IF NOT EXISTS c_event_claims_event_idx ON c_event_claims(event, created_at DESC);
"""

# 被邀请账号去重后的人数（含复核中的数量）。可按邀请人过滤；uid=None 时给后台全量。
_COUNT_SQL = """
WITH att AS (
  SELECT i.inviter_id AS uid, i.invitee_id AS inv, (i.status='frozen' OR i.flags<>'') AS flagged
    FROM c_ref_invites i WHERE i.status IN ('pending','valid','frozen')
  UNION ALL
  SELECT p.user_id, u.id, FALSE
    FROM c_users u JOIN c_promoters p ON p.code=u.promoter_code WHERE p.user_id IS NOT NULL
)
SELECT a.uid, count(DISTINCT a.inv) AS n, count(DISTINCT a.inv) FILTER (WHERE a.flagged) AS flagged
  FROM att a JOIN c_users iu ON iu.id=a.inv AND NOT iu.is_banned AND NOT COALESCE(iu.is_bot, FALSE)
 WHERE a.inv<>a.uid {where}
 GROUP BY a.uid
"""


def counts_for(conn, uid):
    r = q(conn, _COUNT_SQL.format(where="AND a.uid=%s"), (uid,), one=True)
    return (int(r["n"]), int(r["flagged"])) if r else (0, 0)


def counts_all(conn):
    return {int(r["uid"]): (int(r["n"]), int(r["flagged"])) for r in q(conn, _COUNT_SQL.format(where=""))}


def flag_reasons(conn):
    """每位邀请人的复核原因（来自邀请有奖的冻结/标记说明），仅供后台参考。"""
    rows = q(conn, """SELECT inviter_id, array_agg(DISTINCT x) AS xs FROM (
                        SELECT inviter_id, unnest(ARRAY[NULLIF(reason,''), NULLIF(flags,'')]) AS x FROM c_ref_invites
                        WHERE status IN ('pending','valid','frozen') AND (status='frozen' OR flags<>'')) t
                      WHERE x IS NOT NULL GROUP BY inviter_id""")
    return {int(r["inviter_id"]): "；".join(r["xs"]) for r in rows}


def event_open(now=None):
    return (now or datetime.datetime.now(KST)) < CLOSES_AT


def invite_link(code):
    from seo import site_origin
    return "%s%s?inv=%s" % (site_origin(), EVENT_PATH, code)


def render_event_qr_png(url: str, code: str) -> bytes:
    """带标志的邀请二维码 PNG（沿用推广二维码的绘制工具）。"""
    from PIL import Image, ImageDraw
    import io
    from promo import _cjk_font, _paste_logo, _qr_matrix
    qr = _paste_logo(_qr_matrix(url)).resize((480, 480), Image.Resampling.NEAREST)
    f1, f2, f3 = _cjk_font(44, bold=True), _cjk_font(38, bold=True), _cjk_font(38, bold=True)
    side = 720
    canvas = Image.new("RGB", (side, 116 + 480 + 16 + 56 + 12 + 56 + 36), "white")
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, side, 100), fill=(230, 243, 241))

    def centered(text, font, y, fill):
        b = d.textbbox((0, 0), text, font=font)
        d.text(((side - (b[2] - b[0])) // 2, y), text, font=font, fill=fill)

    centered("旅韩助手 · 首尔国际电影大奖", f1, 24, (20, 110, 104))
    canvas.paste(qr, ((side - 480) // 2, 116))
    centered("扫码注册，帮朋友凑齐 5 人", f2, 116 + 480 + 16, (26, 35, 48))
    centered("邀请码 " + code, f3, 116 + 480 + 16 + 56 + 12, (20, 110, 104))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _claim_json(r):
    if not r:
        return None
    return {"id": r["id"], "status": r["status"], "label": STATUS_LABEL.get(r["status"], r["status"]),
            "time": _ts(r["created_at"]), "note": r["note"]}


@router.get(EVENT_PATH)
def p_event_gma():
    return _page("event-gma.html", EVENT_PATH, extra={
        "title": "首尔国际电影大奖 12月17日 · 邀请好友领入场名额 · 旅韩助手",
        "description": "2026 第14届首尔国际电影大奖（12月17日，广津华克山庄）。邀请 5 位好友注册旅韩助手，即可申请免费入场名额，名额有限，最终以主办方确认为准。"})


@router.get("/api/event/gma/me")
def api_gma_me(req: Request):
    base = {"event": EVENT_KEY, "need": NEED, "open": event_open(), "closes": "2026-12-17 17:00（韩国时间）"}
    with db() as conn:
        u = current_user(conn, req)
        if not u:
            return dict(base, user=None)
        from me import _code, _touch_device
        _touch_device(conn, u["id"], req)   # 记录邀请人的设备，沿用「同设备互邀不计入」
        code = _code(conn, u["id"])
        n, fl = counts_for(conn, u["id"])
        claim = q(conn, "SELECT * FROM c_event_claims WHERE event=%s AND user_id=%s", (EVENT_KEY, u["id"]), one=True)
    return dict(base, user={"id": u["id"], "nickname": u["nickname"]}, count=n, qualified=n >= NEED, code=code,
                link=invite_link(code), claim=_claim_json(claim))


@router.get("/api/event/gma/qr.png")
def api_gma_qr(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        from me import _code
        code = _code(conn, u["id"])
    data = render_event_qr_png(invite_link(code), code)
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store",
                    "Content-Disposition": 'inline; filename="lvhan-event-%s.png"' % code})


@router.post("/api/event/gma/claim")
async def api_gma_claim(req: Request):
    csrf(req)
    d = await _json(req)
    note = clean_text(d.get("note") or "", 100, "联系备注", minlen=0)
    with db() as conn:
        u = need_user(conn, req)
        limit("gmaclaim:%s" % u["id"], 10, 3600)
        claim = q(conn, "SELECT * FROM c_event_claims WHERE event=%s AND user_id=%s", (EVENT_KEY, u["id"]), one=True)
        if claim:   # 幂等：已申请过就原样返回
            return {"ok": True, "already": True, "claim": _claim_json(claim)}
        if not event_open():
            raise HTTPException(400, "活动已开始，申请已截止")
        n, fl = counts_for(conn, u["id"])
        if n < NEED:
            raise HTTPException(400, "还差 %d 位好友注册，邀请满 %d 位后再申请" % (NEED - n, NEED))
        claim = q(conn, """INSERT INTO c_event_claims(event,user_id,referral_count,flagged_count,note)
                           VALUES (%s,%s,%s,%s,%s) ON CONFLICT (event,user_id) DO NOTHING RETURNING *""",
                  (EVENT_KEY, u["id"], n, fl, note), one=True)
        if not claim:
            claim = q(conn, "SELECT * FROM c_event_claims WHERE event=%s AND user_id=%s", (EVENT_KEY, u["id"]), one=True)
    return {"ok": True, "already": False, "claim": _claim_json(claim)}


# ---------------- 后台 ----------------
def _who(r):
    return "#%s %s" % (r.get("member_no") or r["id"], r["nickname"])


@router.get("/api/admin/event-gma")
def api_admin_gma(req: Request):
    with db() as conn:
        need_admin(conn, req)
        cnt = counts_all(conn)
        why = flag_reasons(conn)
        claims = q(conn, """SELECT c.id,c.user_id,c.referral_count,c.flagged_count,c.note,c.status,c.admin_note,c.created_at,c.reviewed_at,
                                   u.id AS uid2,u.nickname,u.member_no,u.email,u.phone
                            FROM c_event_claims c JOIN c_users u ON u.id=c.user_id
                            WHERE c.event=%s ORDER BY (c.status='pending') DESC, c.id DESC LIMIT 300""", (EVENT_KEY,))
        claimed = {r["user_id"] for r in claims}
        qual_ids = [uid for uid, (n, _f) in cnt.items() if n >= NEED]
        quals = q(conn, "SELECT id,nickname,member_no,email,phone,created_at FROM c_users WHERE id = ANY(%s) ORDER BY id", (qual_ids,)) if qual_ids else []
    st = {k: sum(1 for r in claims if r["status"] == k) for k in STATUS_LABEL}
    return {
        "need": NEED, "open": event_open(),
        "stats": dict(st, claims=len(claims), qualified=len(quals), qualified_unclaimed=sum(1 for r in quals if r["id"] not in claimed)),
        "claims": [{"id": r["id"], "who": _who({"member_no": r["member_no"], "id": r["uid2"], "nickname": r["nickname"]}),
                    "account": r["email"] or r["phone"] or "", "count": cnt.get(r["user_id"], (0, 0))[0],
                    "flagged": cnt.get(r["user_id"], (0, 0))[1], "why": why.get(r["user_id"], ""), "count_at_claim": r["referral_count"],
                    "note": r["note"], "status": r["status"], "label": STATUS_LABEL.get(r["status"], r["status"]),
                    "admin_note": r["admin_note"], "time": _ts(r["created_at"])} for r in claims],
        "qualified": [{"who": _who(r), "account": r["email"] or r["phone"] or "", "count": cnt[r["id"]][0], "flagged": cnt[r["id"]][1], "why": why.get(r["id"], ""),
                       "claimed": r["id"] in claimed, "joined": _ts(r["created_at"])} for r in quals],
    }


@router.post("/api/admin/event-gma/claims/{cid}")
async def api_admin_gma_claim(cid: int, req: Request):
    csrf(req)
    d = await _json(req)
    act = d.get("action")
    if act not in ("confirm", "decline", "pending"):
        raise HTTPException(400, "参数错误")
    status = {"confirm": "confirmed", "decline": "declined", "pending": "pending"}[act]
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, """UPDATE c_event_claims SET status=%s, admin_note=%s, reviewed_at=now() WHERE id=%s AND event=%s RETURNING id""",
              (status, clean_text(d.get("note") or "", 100, "备注", minlen=0), cid, EVENT_KEY), one=True)
        if not r:
            raise HTTPException(404, "记录不存在")
    return {"ok": True, "status": status}
