"""会员付款截图：用户上传付款截图（自动带会员编号），管理员在后台「付款审核」查看并一键开通或驳回。
数据库只做加法：新增 c_pay_proofs 表。截图压缩为 JPEG（去除 EXIF）后存库。"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from community import (
    MAX_IMG_BYTES, csrf, db, get_settings, ip_of, limit, need_admin, need_user, process_image, q, _json, _ts,
)

router = APIRouter()

KIND_LABELS = {
    "t1": "会员",
    "t2": "高级会员",
    "m_month": "商家会员月付",
    "m_year": "商家会员年付",
    "extra": "商家增发帖",
    "chk_deposit": "炸鸡商家保证金",
    "banner": "横幅广告位",
}
PRICE_KEY = {"t1": "price_t1", "t2": "price_t2", "m_month": "price_m_month", "m_year": "price_m_year", "extra": "price_extra"}
EXTRA_MAX = 20
NOTE_MAX = 100
MAX_PENDING = 3

MIGRATE_PAY_PROOF = """
CREATE TABLE IF NOT EXISTS c_pay_proofs (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  extra_n INT NOT NULL DEFAULT 1,
  expect_cents INT,
  paid_cents INT,
  note TEXT,
  img BYTEA,
  thumb BYTEA,
  status TEXT NOT NULL DEFAULT 'pending',
  admin_note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS c_pay_proofs_user_idx ON c_pay_proofs(user_id, id DESC);
CREATE INDEX IF NOT EXISTS c_pay_proofs_status_idx ON c_pay_proofs(status, id DESC);
"""


def _cents(v) -> int:
    try:
        return max(0, round(float(v) * 100))
    except (TypeError, ValueError):
        return 0


def expect_cents(conn, u, kind, n=1, ref_id=0) -> int:
    if kind == "banner":
        from merch import banner_price_cents
        return banner_price_cents(conn, u["id"], ref_id)
    if kind == "chk_deposit":
        from chicken_merchant import DEPOSIT_YUAN
        return DEPOSIT_YUAN * 100
    st = get_settings(conn)
    c = _cents(st.get(PRICE_KEY[kind]))
    if kind == "extra":
        return c * n
    if kind == "t2" and int(u.get("base_tier", u.get("tier", 0)) or 0) == 1:   # 会员升级高级会员补差价
        c = max(0, c - _cents(st.get("price_t1")))
    return c


def _out(r, admin=False):
    o = {"id": r["id"], "kind": r["kind"], "kind_label": KIND_LABELS.get(r["kind"], r["kind"]), "extra_n": r["extra_n"],
         "expect": round((r.get("expect_cents") or 0) / 100, 2), "status": r["status"],
         "admin_note": r.get("admin_note") or "", "time": _ts(r["created_at"]), "reviewed_at": _ts(r.get("reviewed_at"))}
    if admin:
        o.update({"user_id": r["user_id"], "member_no": r.get("member_no"), "nickname": r.get("nickname") or "",
                  "account": r.get("account") or "", "note": r.get("note") or "", "ref_id": r.get("ref_id"),
                  "paid": round((r.get("paid_cents") or 0) / 100, 2) if r.get("paid_cents") else None,
                  "img": f"/api/admin/pay-proofs/{r['id']}/img", "thumb": f"/api/admin/pay-proofs/{r['id']}/img?t=1"})
    return o


@router.get("/api/pay-proof/meta")
def api_meta(req: Request):
    with db() as conn:
        st = get_settings(conn)
    from chicken_merchant import DEPOSIT_YUAN
    return {"kinds": [{"key": k, "label": v, "price": st.get(PRICE_KEY[k]) if k in PRICE_KEY else (str(DEPOSIT_YUAN) if k == "chk_deposit" else "按报价")} for k, v in KIND_LABELS.items()],
            "extra_max": EXTRA_MAX}


@router.get("/api/pay-proof/mine")
def api_mine(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        rows = q(conn, "SELECT id,user_id,kind,extra_n,expect_cents,status,admin_note,created_at,reviewed_at FROM c_pay_proofs WHERE user_id=%s ORDER BY id DESC LIMIT 10", (u["id"],))
    return {"items": [_out(r) for r in rows]}


@router.post("/api/pay-proof")
async def api_submit(req: Request, kind: str = Form(...), extra_n: int = Form(1), note: str = Form(""),
                     ref_id: int = Form(0), image: UploadFile = File(...)):
    csrf(req)
    if kind not in KIND_LABELS:
        raise HTTPException(400, "请选择开通项目")
    n = extra_n if kind == "extra" else 1
    if n < 1 or n > EXTRA_MAX:
        raise HTTPException(400, f"增发篇数应为 1–{EXTRA_MAX}")
    note = (note or "").strip()[:NOTE_MAX]
    with db() as conn:
        u = need_user(conn, req)
        if u.get("is_banned"):
            raise HTTPException(403, "账号已被限制")
        if kind == "extra" and not u.get("merchant"):
            raise HTTPException(400, "增发帖仅限有效商家会员，请先开通商家会员")
        if kind == "chk_deposit":
            app = q(conn, "SELECT deposit_status FROM c_chicken_merchants WHERE user_id=%s", (u["id"],), one=True)
            if not app:
                raise HTTPException(400, "请先提交炸鸡商家入驻申请，再上传保证金截图")
            if app["deposit_status"] == "paid":
                raise HTTPException(400, "保证金已确认收到，无需重复提交")
        if kind == "banner":
            expect_cents(conn, u, kind, 1, ref_id)   # 先校验横幅归属和是否已付
        limit("payproof-try:" + str(u["id"]), 10, 3600, "操作太频繁，请一小时后再试")   # 含失败尝试
        cnt = q(conn, "SELECT count(*) AS n FROM c_pay_proofs WHERE user_id=%s AND status='pending'", (u["id"],), one=True)["n"]
        if cnt >= MAX_PENDING:
            raise HTTPException(429, "你已有待审核的付款截图，请等待管理员处理")
        ctype = (image.content_type or "").lower()
        if ctype and not ctype.startswith("image/"):
            raise HTTPException(400, "只能上传图片（JPG/PNG/WebP）")
        big, th, _w, _h = process_image(await image.read(MAX_IMG_BYTES + 1))
        limit("payproof:" + str(u["id"]), 3, 3600, "提交太频繁，请一小时后再试")
        limit("payproof-ip:" + ip_of(req), 20, 86400, "今天提交次数太多，请明天再试或联系管理员")
        exp = expect_cents(conn, u, kind, n, ref_id)
        r = q(conn, """INSERT INTO c_pay_proofs(user_id,kind,extra_n,expect_cents,note,img,thumb,ref_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,user_id,kind,extra_n,expect_cents,status,admin_note,created_at,reviewed_at""",
              (u["id"], kind, n, exp, note or None, big, th, ref_id if kind == "banner" else None), one=True)
    return {"ok": True, "item": _out(r), "member_no": u.get("member_no")}


# ---------------- 管理员 ----------------
@router.get("/api/admin/pay-proofs")
def api_admin_list(req: Request, status: str = "pending"):
    if status not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        sql = """SELECT p.id,p.user_id,p.kind,p.extra_n,p.expect_cents,p.paid_cents,p.note,p.status,p.admin_note,p.created_at,p.reviewed_at,p.ref_id,
                        u.member_no,u.nickname,COALESCE(u.email,u.phone) AS account FROM c_pay_proofs p JOIN c_users u ON u.id=p.user_id"""
        args = ()
        if status != "all":
            sql += " WHERE p.status=%s"; args = (status,)
        rows = q(conn, sql + " ORDER BY p.id DESC LIMIT 200", args)
        pending = q(conn, "SELECT count(*) AS n FROM c_pay_proofs WHERE status='pending'", one=True)["n"]
    return {"items": [_out(r, admin=True) for r in rows], "pending": pending}


@router.get("/api/admin/pay-proofs/{pid}/img")
def api_admin_img(req: Request, pid: int, t: int = 0):
    col = "thumb" if t else "img"
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, f"SELECT {col} AS d FROM c_pay_proofs WHERE id=%s", (pid,), one=True)
    if not r or not r["d"]:
        raise HTTPException(404, "图片不存在")
    return Response(bytes(r["d"]), media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/api/admin/pay-proofs/{pid}")
async def api_admin_review(req: Request, pid: int):
    csrf(req)
    d = await _json(req)
    act = d.get("action")
    if act not in ("approve", "reject"):
        raise HTTPException(400, "未知操作")
    note = str(d.get("note") or "").strip()[:200]
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT * FROM c_pay_proofs WHERE id=%s FOR UPDATE", (pid,), one=True)
        if not r:
            raise HTTPException(404, "找不到这条记录")
        if r["status"] != "pending":
            raise HTTPException(409, "这条记录已处理过")
        if act == "reject":
            q(conn, "UPDATE c_pay_proofs SET status='rejected', admin_note=%s, reviewed_at=now() WHERE id=%s",
              (note or "未核对到这笔付款，请联系管理员", pid))
            return {"ok": True, "status": "rejected"}
        amount = d.get("amount")
        if amount in (None, ""):
            amount = (r["expect_cents"] or 0) / 100
        result = {"ok": True, "status": "approved"}
        if r["kind"] == "banner":
            from merch import mark_banner_paid
            if r.get("ref_id"):
                mark_banner_paid(conn, r["ref_id"])
            result["banner"] = "paid"
            paid = _cents(amount)
        elif r["kind"] == "chk_deposit":
            from chicken_merchant import mark_deposit_paid
            mark_deposit_paid(conn, r["user_id"], pid)
            result["deposit"] = "paid"
            paid = _cents(amount)
        elif r["kind"] == "extra":
            row = q(conn, "UPDATE c_users SET merchant_credits=merchant_credits+%s WHERE id=%s RETURNING merchant_credits",
                    (r["extra_n"], r["user_id"]), one=True)
            q(conn, "INSERT INTO c_merchant_log(user_id,action,detail) VALUES (%s,'credits',%s)", (r["user_id"], str(r["extra_n"])))
            result["credits"] = row["merchant_credits"]
            paid = _cents(amount)
        else:
            from promo import confirm_payment, parse_amount_cents
            paid = parse_amount_cents(amount)
            result["payment"] = confirm_payment(conn, r["user_id"], r["kind"], amount, f"付款截图 #{pid}")
        q(conn, "UPDATE c_pay_proofs SET status='approved', paid_cents=%s, admin_note=%s, reviewed_at=now() WHERE id=%s",
          (paid, note or None, pid))
    return result
