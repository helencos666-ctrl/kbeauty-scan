"""旅韩助手 · 交友/相亲（第三期）
- 交友资料（每人一份）：昵称、性别、年龄、身高、城市/区域、签证身份（可选）、交友目的、自我介绍、最多 6 张照片、公开开关
- 旅韩交友筛选、资料详情、打招呼、私信（站内消息，带未读数）
- 安全：拉黑、举报、打招呼每日上限、对方回复前限发条数、诈骗关键词提醒并自动标记给管理员
- 权限分档（可改下面的常量）：游客/免费只能看模糊卡片；会员可看资料；高级会员/商家可发布资料、打招呼、私信
照片复用社区图片流程（压缩、去 EXIF/定位，存数据库）。
"""
import io, re

from fastapi import APIRouter, Request, Response, HTTPException, UploadFile, File
from PIL import Image, ImageFilter

import community as C
from community import db, q, csrf, limit, need_user, need_tier, current_user, clean_text, process_image, get_settings, _ts, ip_of, MAX_IMG_BYTES, _json

router = APIRouter()

# ---------------- 可配置常量 ----------------
FRIEND_VIEW_TIER = 1          # 查看交友资料、清晰照片
FRIEND_CREATE_TIER = 2        # 发布交友资料
FRIEND_MSG_TIER = 2           # 打招呼、发私信
GREET_DAILY_LIMIT = 20        # 每人每 24 小时最多打招呼次数
MSG_DAILY_LIMIT = 300         # 每人每 24 小时最多私信条数
MAX_UNANSWERED = 3            # 对方回复前，最多连续发送几条（防骚扰）
GREET_REQUIRES_PROFILE = True # 打招呼前需先发布自己的交友资料
MAX_PROFILE_PHOTOS = 6
LOCKED_PREVIEW_N = 12         # 未开通会员时显示几张模糊卡片
AGE_MIN, AGE_MAX = 18, 80     # 仅限成年人
HEIGHT_MIN, HEIGHT_MAX = 140, 220
GENDERS = {"m": "男", "f": "女"}
PURPOSES = {"friend": "交友", "date": "相亲", "buddy": "找搭子", "lang": "语言交换"}
CITIES = ["首尔", "釜山", "仁川", "京畿", "济州", "大邱", "大田", "光州", "蔚山", "其他"]
VISA_TYPES = ["游客/免签", "留学签证", "工作签证", "F 签/永住", "韩国籍", "其他", "不透露"]
DEFAULT_GREETING = "你好，看了你的资料，想认识一下 👋"
# 私信诈骗高危词：命中后消息照常送达，但双方看到防骗提醒，并自动提交管理员
SCAM_WORDS = ["转账", "汇款", "打款", "打钱", "借钱", "借款", "贷款", "投资", "理财", "收益", "回报率", "稳赚", "带你赚", "赚钱项目",
              "币圈", "加密货币", "虚拟币", "数字货币", "比特币", "泰达币", "usdt", "btc", "eth", "交易所", "合约", "彩票", "博彩",
              "刷单", "返利", "垫付", "保证金", "微信转账", "支付宝转账", "银行卡", "验证码", "充值", "代付", "换汇"]
# 资料自我介绍里不允许留联系方式或高危词（联系方式请在私信里自行决定是否交换）
CONTACT_WORDS = ["微信号", "加微", "加v", "vx", "wx:", "wx：", "kakao id", "kakaotalk id", "line id", "qq号", "电话号码", "手机号"]
WECHAT_RE = re.compile(r"(微信|weixin|wechat|v信|威信)[:：]?\s*[a-z][-_a-z0-9]{5,19}", re.I)
PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9}|01[016789][-\s]?\d{3,4}[-\s]?\d{4})(?!\d)")

def scam_hits(text):
    t = (text or "").lower().replace(" ", "")
    hits = []
    for w in SCAM_WORDS:
        if w.isascii():
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", (text or "").lower()):
                hits.append(w)
        elif w in t:
            hits.append(w)
    return hits

def contact_hits(text):
    t = (text or "").lower().replace(" ", "")
    hits = [w for w in CONTACT_WORDS if w.replace(" ", "") in t]
    if WECHAT_RE.search(text or ""):
        hits.append("微信号")
    if PHONE_RE.search(text or ""):
        hits.append("电话号码")
    return hits

def options():
    from buddy_verify import BUDDY_DISCLAIMER, REPORT_REASONS
    return {"genders": GENDERS, "purposes": PURPOSES, "cities": CITIES, "visa_types": VISA_TYPES,
            "age_min": AGE_MIN, "age_max": AGE_MAX, "greet_daily_limit": GREET_DAILY_LIMIT, "max_photos": MAX_PROFILE_PHOTOS,
            "tiers": {"view": FRIEND_VIEW_TIER, "create": FRIEND_CREATE_TIER, "message": FRIEND_MSG_TIER},
            "disclaimer": BUDDY_DISCLAIMER, "report_reasons": REPORT_REASONS}

def _tier_msg(conn, tier, action):
    st = get_settings(conn)
    return (f"开通会员（¥{st['price_t1']}）后才能{action}" if tier == 1 else f"开通高级会员（¥{st['price_t2']}）后才能{action}")

def blocked_between(conn, a, b):
    r = q(conn, """SELECT bool_or(blocker_id=%s) AS i_blocked, bool_or(blocker_id=%s) AS they_blocked FROM c_blocks
                   WHERE (blocker_id=%s AND blocked_id=%s) OR (blocker_id=%s AND blocked_id=%s)""", (a, b, a, b, b, a), one=True)
    return bool(r and r["i_blocked"]), bool(r and r["they_blocked"])

def _photos(conn, uid):
    return [p["id"] for p in q(conn, "SELECT id FROM c_dating_photos WHERE user_id=%s ORDER BY pos, id", (uid,))]

def _profile_out(r, photos, full=True):
    o = {"uid": r["user_id"], "member_no": r.get("member_no") or r["user_id"], "nickname": r["nickname"], "gender": r["gender"], "gender_name": GENDERS.get(r["gender"], ""),
         "age": r["age"], "height": r["height"], "city": r["city"], "area": r["area"] or "", "purpose": r["purpose"],
         "purpose_name": PURPOSES.get(r["purpose"], ""), "verified": bool(r.get("verified")),
         "id_verified": bool(r.get("id_verified")), "photos": photos,
         "cover": photos[0] if photos else None, "has_avatar": bool(r.get("has_avatar"))}
    if full:
        o.update({"visa": r["visa"] or "", "intro": r["intro"], "visible": r["visible"], "hidden": r["hidden_by_admin"] or r.get("auto_hidden"), "auto_hidden": bool(r.get("auto_hidden")),
                  "updated": _ts(r["updated_at"])})
    return o

LIST_SQL = """SELECT d.*, u.verified, u.id_verified, u.member_no, u.buddy_banned_until,
    (u.avatar IS NOT NULL OR COALESCE(u.is_bot, FALSE)) AS has_avatar,
    (SELECT id FROM c_dating_photos ph WHERE ph.user_id=d.user_id ORDER BY pos, id LIMIT 1) AS cover
    FROM c_dating_profiles d JOIN c_users u ON u.id=d.user_id
    WHERE d.visible AND NOT d.hidden_by_admin AND NOT COALESCE(d.auto_hidden, FALSE) AND NOT u.is_banned"""

# ---------------- 交友卡片列表 ----------------
@router.get("/api/friends/options")
def api_options():
    return options()

@router.get("/api/friends/list")
def api_list(req: Request, gender: str = "", age_min: int = 0, age_max: int = 0, city: str = "", purpose: str = "",
             offset: int = 0, limit_n: int = 20):
    limit_n = max(1, min(limit_n, 30))
    offset = max(0, min(offset, 500))
    with db() as conn:
        u = current_user(conn, req)
        tier = u["tier"] if u else 0
        locked = tier < FRIEND_VIEW_TIER
        cond, args = [], []
        if gender in GENDERS:
            cond.append("d.gender=%s"); args.append(gender)
        if purpose in PURPOSES:
            cond.append("d.purpose=%s"); args.append(purpose)
        if city in CITIES:
            cond.append("d.city=%s"); args.append(city)
        if age_min:
            cond.append("d.age>=%s"); args.append(age_min)
        if age_max:
            cond.append("d.age<=%s"); args.append(age_max)
        if u:
            cond.append("d.user_id<>%s"); args.append(u["id"])
            cond.append("""NOT EXISTS (SELECT 1 FROM c_blocks b WHERE (b.blocker_id=%s AND b.blocked_id=d.user_id)
                                                                 OR (b.blocker_id=d.user_id AND b.blocked_id=%s))""")
            args += [u["id"], u["id"]]
        if locked:
            offset, limit_n = 0, LOCKED_PREVIEW_N
        rows = q(conn, LIST_SQL + "".join(" AND " + c for c in cond) +
                 " ORDER BY u.verified DESC, d.updated_at DESC LIMIT %s OFFSET %s", (*args, limit_n + 1, offset))
    more = len(rows) > limit_n and not locked
    cards = []
    for r in rows[:limit_n]:
        if locked:   # 未开通会员：不返回编号、完整昵称和清晰照片
            cards.append({"locked": True, "nickname": (r["nickname"][:1] + "**"), "gender": r["gender"],
                          "gender_name": GENDERS.get(r["gender"], ""), "age": r["age"], "height": r["height"], "city": r["city"],
                          "purpose_name": PURPOSES.get(r["purpose"], ""), "verified": bool(r["verified"]), "blur": r["cover"]})
        else:
            o = _profile_out(r, [r["cover"]] if r["cover"] else [], full=False)
            o["intro"] = r["intro"][:40]
            if r.get("purpose") == "buddy" and not (u and (u["id"] == r["user_id"] or (u.get("id_verified") and not __import__("buddy_verify").buddy_ban_active(u)))):
                from buddy_verify import BUDDY_DISCLAIMER, buddy_ban_active
                cards.append({"teaser": True, "need_verify": True, "purpose": "buddy", "purpose_name": PURPOSES["buddy"],
                              "city": r["city"], "gender": r["gender"], "gender_name": GENDERS.get(r["gender"], ""),
                              "age": r["age"], "nickname": (r["nickname"][:1] + "**"),
                              "id_verified": bool(r.get("id_verified")), "disclaimer": BUDDY_DISCLAIMER})
            else:
                cards.append(o)
    from buddy_verify import BUDDY_DISCLAIMER
    return {"locked": locked, "login_required": not u, "tier_required": FRIEND_VIEW_TIER, "cards": cards, "more": more,
            "next": offset + limit_n, "disclaimer": BUDDY_DISCLAIMER}

@router.get("/api/friends/profile/{uid}")
def api_profile(req: Request, uid: int):
    with db() as conn:
        u = current_user(conn, req)
        if not u:
            raise HTTPException(401, "请先登录，开通会员后查看交友资料")
        r = q(conn, """SELECT d.*, u.verified, u.id_verified, u.is_banned, u.member_no, (CASE WHEN u.merchant_until>now() THEN 3 ELSE u.tier END) AS tier,
                              (u.avatar IS NOT NULL OR COALESCE(u.is_bot, FALSE)) AS has_avatar
                       FROM c_dating_profiles d JOIN c_users u ON u.id=d.user_id WHERE d.user_id=%s""", (uid,), one=True)
        mine = uid == u["id"]
        if not mine and u["tier"] < FRIEND_VIEW_TIER:
            raise HTTPException(403, _tier_msg(conn, FRIEND_VIEW_TIER, "查看交友资料"))
        if not r or (not mine and (r["is_banned"] or not r["visible"] or r["hidden_by_admin"] or r.get("auto_hidden"))):
            raise HTTPException(404, "资料不存在或已隐藏")
        if r.get("purpose") == "buddy" and not mine:
            from buddy_verify import is_id_verified, buddy_ban_active, BUDDY_DISCLAIMER
            if buddy_ban_active(u) or not is_id_verified(u):
                return {"teaser": True, "need_verify": True, "purpose": "buddy", "purpose_name": PURPOSES["buddy"],
                        "city": r["city"], "gender": r.get("gender"), "age": r["age"],
                        "nickname": (r["nickname"][:1] + "**"), "id_verified": bool(r.get("id_verified")),
                        "disclaimer": BUDDY_DISCLAIMER, "can_message": False}
        i_blocked, they_blocked = blocked_between(conn, u["id"], uid)
        if they_blocked and not mine:
            raise HTTPException(404, "资料不存在或已隐藏")
        photos = _photos(conn, uid)
        following = bool(q(conn, "SELECT 1 AS x FROM c_follows WHERE follower_id=%s AND followee_id=%s", (u["id"], uid), one=True))
        a, b = sorted((u["id"], uid))
        th = q(conn, "SELECT id FROM c_threads WHERE user_a=%s AND user_b=%s", (a, b), one=True)
        my_profile = q(conn, "SELECT 1 AS x FROM c_dating_profiles WHERE user_id=%s", (u["id"],), one=True) is not None
    o = _profile_out(r, photos)
    o.update({"mine": mine, "author_tier": r["tier"], "following": following, "blocked": i_blocked,
              "thread_id": th["id"] if th else None, "my_tier": u["tier"], "has_my_profile": my_profile,
              "can_message": (not mine) and u["tier"] >= FRIEND_MSG_TIER and not i_blocked})
    return o

# ---------------- 我的交友资料 ----------------
@router.get("/api/friends/me")
def api_my_profile(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        r = q(conn, """SELECT d.*, u.verified, u.member_no,
                              (u.avatar IS NOT NULL OR COALESCE(u.is_bot, FALSE)) AS has_avatar
                       FROM c_dating_profiles d JOIN c_users u ON u.id=d.user_id WHERE d.user_id=%s""",
              (u["id"],), one=True)
        photos = _photos(conn, u["id"]) if r else []
        st = get_settings(conn)
    return {"profile": _profile_out(r, photos) if r else None, "can_edit": u["tier"] >= FRIEND_CREATE_TIER,
            "default_nickname": u["nickname"], "options": options(), "price_t2": st["price_t2"]}

def _int(v, field, lo, hi, required=True):
    if v in (None, "") and not required:
        return None
    try:
        n = int(v)
    except Exception:
        raise HTTPException(400, f"请填写{field}")
    if n < lo or n > hi:
        raise HTTPException(400, f"{field}需在 {lo}–{hi} 之间" + ("（仅限成年人）" if field == "年龄" else ""))
    return n

@router.post("/api/friends/me")
async def api_save_profile(req: Request):
    csrf(req)
    d = await _json(req)
    nick = clean_text(d.get("nickname"), 16, "昵称", 2)
    if re.search(r"管理员|官方|admin", nick, re.I):
        raise HTTPException(400, "昵称不能包含“管理员/官方”")
    gender = d.get("gender")
    if gender not in GENDERS:
        raise HTTPException(400, "请选择性别")
    age = _int(d.get("age"), "年龄", AGE_MIN, AGE_MAX)
    height = _int(d.get("height"), "身高", HEIGHT_MIN, HEIGHT_MAX, required=False)
    city = d.get("city")
    if city not in CITIES:
        raise HTTPException(400, "请选择所在城市")
    area = clean_text(d.get("area"), 20, "区域", 0) or None
    visa = d.get("visa") or None
    if visa is not None and visa not in VISA_TYPES:
        raise HTTPException(400, "签证/身份选项无效")
    purpose = d.get("purpose")
    if purpose not in PURPOSES:
        raise HTTPException(400, "请选择交友目的")
    intro = clean_text(d.get("intro"), 500, "自我介绍", 10)
    bad = contact_hits(nick + "\n" + (area or "") + "\n" + intro) + scam_hits(intro)
    if bad:
        raise HTTPException(400, "资料里不能留联系方式或涉及转账/投资等内容（" + "、".join(bad[:3]) + "）。联系方式请在私信中自行判断是否交换。")
    visible = bool(d.get("visible", True))
    with db() as conn:
        u = need_tier(conn, req, FRIEND_CREATE_TIER, "开通高级会员（¥{t2}）后才能发布交友资料")
        if purpose == "buddy":
            from buddy_verify import require_buddy_ok
            require_buddy_ok(u, "发布旅游搭子资料")
        limit("fp:" + str(u["id"]), 20, 3600, "保存太频繁，请稍后再试")
        q(conn, """INSERT INTO c_dating_profiles(user_id,nickname,gender,age,height,city,area,visa,purpose,intro,visible)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id) DO UPDATE SET nickname=EXCLUDED.nickname, gender=EXCLUDED.gender, age=EXCLUDED.age,
                     height=EXCLUDED.height, city=EXCLUDED.city, area=EXCLUDED.area, visa=EXCLUDED.visa, purpose=EXCLUDED.purpose,
                     intro=EXCLUDED.intro, visible=EXCLUDED.visible, updated_at=now()""",
          (u["id"], nick, gender, age, height, city, area, visa, purpose, intro, visible))
    return {"ok": True}

@router.post("/api/friends/me/visible")
async def api_profile_visible(req: Request):
    """公开/隐藏自己的资料（降级为会员后也可以随时隐藏）"""
    csrf(req)
    d = await _json(req)
    with db() as conn:
        u = need_user(conn, req)
        r = q(conn, "UPDATE c_dating_profiles SET visible=%s WHERE user_id=%s RETURNING visible", (bool(d.get("visible")), u["id"]), one=True)
        if not r:
            raise HTTPException(404, "你还没有交友资料")
    return {"visible": r["visible"]}

@router.delete("/api/friends/me")
def api_delete_profile(req: Request):
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        q(conn, "DELETE FROM c_dating_photos WHERE user_id=%s", (u["id"],))
        q(conn, "DELETE FROM c_dating_profiles WHERE user_id=%s", (u["id"],))
    return {"ok": True}

@router.post("/api/friends/me/photos")
async def api_add_photo(req: Request, image: UploadFile = File(...)):
    csrf(req)
    with db() as conn:     # 先确认身份再处理图片
        need_tier(conn, req, FRIEND_CREATE_TIER, "开通高级会员（¥{t2}）后才能上传交友照片")
    big, th, w, h = process_image(await image.read(MAX_IMG_BYTES + 1))
    with db() as conn:
        u = need_tier(conn, req, FRIEND_CREATE_TIER, "开通高级会员（¥{t2}）后才能上传交友照片")
        if not q(conn, "SELECT 1 AS x FROM c_dating_profiles WHERE user_id=%s", (u["id"],), one=True):
            raise HTTPException(400, "请先保存资料，再上传照片")
        limit("fph:" + str(u["id"]), 30, 3600, "上传太频繁，请稍后再试")
        n = q(conn, "SELECT count(*) AS n, COALESCE(max(pos),0) AS mp FROM c_dating_photos WHERE user_id=%s", (u["id"],), one=True)
        if n["n"] >= MAX_PROFILE_PHOTOS:
            raise HTTPException(400, f"最多 {MAX_PROFILE_PHOTOS} 张照片")
        r = q(conn, "INSERT INTO c_dating_photos(user_id,pos,data,thumb,width,height) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
              (u["id"], n["mp"] + 1, big, th, w, h), one=True)
        q(conn, "UPDATE c_dating_profiles SET updated_at=now() WHERE user_id=%s", (u["id"],))
    return {"ok": True, "id": r["id"]}

@router.delete("/api/friends/me/photos/{pid}")
def api_del_photo(req: Request, pid: int):
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        if not q(conn, "DELETE FROM c_dating_photos WHERE id=%s AND user_id=%s RETURNING id", (pid, u["id"]), one=True):
            raise HTTPException(404, "照片不存在")
    return {"ok": True}

@router.post("/api/friends/me/photos/{pid}/cover")
def api_cover_photo(req: Request, pid: int):
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        r = q(conn, """UPDATE c_dating_photos SET pos=(SELECT COALESCE(min(pos),0)-1 FROM c_dating_photos WHERE user_id=%s)
                       WHERE id=%s AND user_id=%s RETURNING id""", (u["id"], pid, u["id"]), one=True)
        if not r:
            raise HTTPException(404, "照片不存在")
    return {"ok": True}

def _blurred(thumb: bytes) -> bytes:
    im = Image.open(io.BytesIO(thumb)).convert("RGB")
    w, h = im.size
    small = im.resize((max(1, w // 24), max(1, h // 24)), Image.BILINEAR)   # 先缩到极小，细节不可恢复
    k = min(1.0, 240 / max(w, h))
    out = small.resize((max(1, int(w * k)), max(1, int(h * k))), Image.BILINEAR).filter(ImageFilter.GaussianBlur(6))
    b = io.BytesIO(); out.save(b, "JPEG", quality=60); return b.getvalue()

@router.get("/api/friends/photo/{pid}")
def api_photo(req: Request, pid: int, t: int = 0, blur: int = 0):
    col = "thumb" if (t or blur) else "data"
    with db() as conn:
        r = q(conn, f"""SELECT ph.{col} AS d, ph.user_id, p.visible, p.hidden_by_admin, u.is_banned
                        FROM c_dating_photos ph JOIN c_dating_profiles p ON p.user_id=ph.user_id JOIN c_users u ON u.id=ph.user_id
                        WHERE ph.id=%s""", (pid,), one=True)
        if not r:
            raise HTTPException(404, "照片不存在")
        cu = current_user(conn, req)
        if cu and cu["id"] == r["user_id"]:
            return Response(bytes(r["d"]), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
        if not r["visible"] or r["hidden_by_admin"] or r.get("auto_hidden") or r["is_banned"]:
            raise HTTPException(404, "照片不存在")
        if blur:
            limit("blur:" + ip_of(req), 240, 60)     # 模糊图要实时计算，限制频率防刷 CPU
            return Response(_blurred(bytes(r["d"])), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})
        if not cu or cu["tier"] < FRIEND_VIEW_TIER:
            raise HTTPException(403, "开通会员后查看")
        if blocked_between(conn, cu["id"], r["user_id"])[1]:
            raise HTTPException(404, "照片不存在")
    return Response(bytes(r["d"]), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

# ---------------- 打招呼与私信 ----------------
def _flag(conn, mid, hits):
    q(conn, "INSERT INTO c_reports(target_type,target_id,user_id,reason) VALUES ('message',%s,NULL,%s)",
      (mid, "系统自动标记：私信含“" + "、".join(hits[:5]) + "”"))

def _warning(hits):
    return ("消息含“" + "、".join(hits[:3]) + "”等敏感词，已向对方显示防骗提醒并通知管理员。本站严禁借交友名义谈转账、投资、借钱。") if hits else None

def _send(conn, tid, sender, recipient, body, kind):
    hits = scam_hits(body)
    r = q(conn, """INSERT INTO c_messages(thread_id,sender_id,recipient_id,kind,body,flagged,flag_words)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
          (tid, sender, recipient, kind, body, bool(hits), "、".join(hits[:5]) or None), one=True)
    q(conn, "UPDATE c_threads SET last_at=now() WHERE id=%s", (tid,))
    if hits:
        _flag(conn, r["id"], hits)
    return r["id"], hits

@router.post("/api/friends/greet/{uid}")
async def api_greet(req: Request, uid: int):
    csrf(req)
    try:
        d = await req.json()
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    body = clean_text(d.get("body") or DEFAULT_GREETING, 100, "招呼语")
    with db() as conn:
        u = need_tier(conn, req, FRIEND_MSG_TIER, "开通高级会员（¥{t2}）后才能打招呼和私信")
        if uid == u["id"]:
            raise HTTPException(400, "不能和自己打招呼")
        t = q(conn, """SELECT d.user_id, d.purpose FROM c_dating_profiles d JOIN c_users x ON x.id=d.user_id
                       WHERE d.user_id=%s AND d.visible AND NOT d.hidden_by_admin AND NOT COALESCE(d.auto_hidden, FALSE) AND NOT x.is_banned""", (uid,), one=True)
        if not t:
            raise HTTPException(404, "资料不存在或已隐藏")
        if t.get("purpose") == "buddy":
            from buddy_verify import require_buddy_ok
            require_buddy_ok(u, "查看和联系旅游搭子")
        i_blocked, they_blocked = blocked_between(conn, u["id"], uid)
        if i_blocked:
            raise HTTPException(403, "你已拉黑对方，解除拉黑后才能打招呼")
        if they_blocked:
            raise HTTPException(403, "对方暂不接收你的消息")
        if GREET_REQUIRES_PROFILE and not q(conn, "SELECT 1 AS x FROM c_dating_profiles WHERE user_id=%s", (u["id"],), one=True):
            raise HTTPException(400, "请先发布你的交友资料，让对方也能了解你")
        n = q(conn, "SELECT count(*) AS n FROM c_messages WHERE sender_id=%s AND kind='greet' AND created_at>now()-interval '1 day'",
              (u["id"],), one=True)["n"]
        if n >= GREET_DAILY_LIMIT:
            raise HTTPException(429, f"24 小时内最多打招呼 {GREET_DAILY_LIMIT} 次，明天再来吧")
        if q(conn, """SELECT 1 AS x FROM c_messages WHERE sender_id=%s AND recipient_id=%s AND kind='greet'
                      AND created_at>now()-interval '1 day'""", (u["id"], uid), one=True):
            raise HTTPException(429, "今天已经和 TA 打过招呼了，等等对方回复吧")
        a, b = sorted((u["id"], uid))
        th = q(conn, """INSERT INTO c_threads(user_a,user_b) VALUES (%s,%s)
                        ON CONFLICT (user_a,user_b) DO UPDATE SET last_at=now() RETURNING id""", (a, b), one=True)
        mid, hits = _send(conn, th["id"], u["id"], uid, body, "greet")
    return {"ok": True, "thread_id": th["id"], "id": mid, "warning": _warning(hits), "left": GREET_DAILY_LIMIT - n - 1}

def _thread(conn, tid, uid):
    th = q(conn, "SELECT * FROM c_threads WHERE id=%s AND (user_a=%s OR user_b=%s)", (tid, uid, uid), one=True)
    if not th:
        raise HTTPException(404, "会话不存在")
    th["other"] = th["user_b"] if th["user_a"] == uid else th["user_a"]
    return th

@router.post("/api/friends/threads/{tid}/messages")
async def api_send(req: Request, tid: int):
    csrf(req)
    d = await _json(req)
    body = clean_text(d.get("body"), 500, "消息")
    with db() as conn:
        u = need_tier(conn, req, FRIEND_MSG_TIER, "开通高级会员（¥{t2}）后才能发私信")
        th = _thread(conn, tid, u["id"])
        other = q(conn, "SELECT is_banned FROM c_users WHERE id=%s", (th["other"],), one=True)
        if not other or other["is_banned"]:
            raise HTTPException(403, "对方账号已被封禁，无法发送")
        i_blocked, they_blocked = blocked_between(conn, u["id"], th["other"])
        if i_blocked:
            raise HTTPException(403, "你已拉黑对方，解除拉黑后才能发送")
        if they_blocked:
            raise HTTPException(403, "对方暂不接收你的消息")
        limit("msg:" + str(u["id"]), 20, 60, "发送太快了，请稍等")
        n = q(conn, "SELECT count(*) AS n FROM c_messages WHERE sender_id=%s AND created_at>now()-interval '1 day'",
              (u["id"],), one=True)["n"]
        if n >= MSG_DAILY_LIMIT:
            raise HTTPException(429, "今天发送的消息太多了，明天再聊吧")
        replied = q(conn, "SELECT 1 AS x FROM c_messages WHERE thread_id=%s AND sender_id=%s AND NOT deleted LIMIT 1",
                    (tid, th["other"]), one=True)
        if not replied:
            mine = q(conn, "SELECT count(*) AS n FROM c_messages WHERE thread_id=%s AND sender_id=%s", (tid, u["id"]), one=True)["n"]
            if mine >= MAX_UNANSWERED:
                raise HTTPException(403, f"对方回复前最多发送 {MAX_UNANSWERED} 条消息，请耐心等待")
        mid, hits = _send(conn, tid, u["id"], th["other"], body, "msg")
    return {"ok": True, "id": mid, "warning": _warning(hits)}

def _names(conn, ids):
    if not ids:
        return {}
    rows = q(conn, """SELECT u.id, COALESCE(d.nickname, u.nickname) AS nick, u.verified, u.is_banned,
                      (u.avatar IS NOT NULL OR COALESCE(u.is_bot, FALSE)) AS has_avatar,
                      (SELECT id FROM c_dating_photos ph WHERE ph.user_id=u.id ORDER BY pos, id LIMIT 1) AS cover,
                      (d.user_id IS NOT NULL AND d.visible AND NOT d.hidden_by_admin) AS has_profile
                      FROM c_users u LEFT JOIN c_dating_profiles d ON d.user_id=u.id WHERE u.id = ANY(%s)""", (list(ids),))
    return {r["id"]: r for r in rows}

@router.get("/api/friends/threads")
def api_threads(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        rows = q(conn, """SELECT t.id, t.last_at, CASE WHEN t.user_a=%(me)s THEN t.user_b ELSE t.user_a END AS other,
                (SELECT body FROM c_messages m WHERE m.thread_id=t.id AND NOT m.deleted ORDER BY m.id DESC LIMIT 1) AS last_body,
                (SELECT count(*) FROM c_messages m WHERE m.thread_id=t.id AND m.recipient_id=%(me)s AND m.read_at IS NULL AND NOT m.deleted) AS unread,
                EXISTS (SELECT 1 FROM c_messages m WHERE m.thread_id=t.id AND m.flagged AND NOT m.deleted) AS flagged
                FROM c_threads t WHERE t.user_a=%(me)s OR t.user_b=%(me)s ORDER BY t.last_at DESC LIMIT 100""", {"me": u["id"]})
        names = _names(conn, {r["other"] for r in rows})
        blocked = {r["blocked_id"] for r in q(conn, "SELECT blocked_id FROM c_blocks WHERE blocker_id=%s", (u["id"],))}
    out = []
    for r in rows:
        if r["last_body"] is None:
            continue
        n = names.get(r["other"], {})
        out.append({"id": r["id"], "other": r["other"], "nickname": n.get("nick", "用户"), "verified": bool(n.get("verified")),
                    "has_avatar": bool(n.get("has_avatar")),
                    "cover": n.get("cover") if n.get("has_profile") else None, "last": r["last_body"][:40],
                    "time": _ts(r["last_at"]), "unread": 0 if r["other"] in blocked else r["unread"], "flagged": r["flagged"],
                    "blocked": r["other"] in blocked, "banned": bool(n.get("is_banned"))})
    return {"threads": out, "unread": sum(t["unread"] for t in out)}

@router.get("/api/friends/threads/{tid}")
def api_thread(req: Request, tid: int):
    with db() as conn:
        u = need_user(conn, req)
        th = _thread(conn, tid, u["id"])
        msgs = q(conn, """SELECT * FROM (SELECT id,sender_id,kind,body,flagged,flag_words,created_at,read_at FROM c_messages
                          WHERE thread_id=%s AND NOT deleted ORDER BY id DESC LIMIT 200) x ORDER BY id""", (tid,))
        q(conn, "UPDATE c_messages SET read_at=now() WHERE thread_id=%s AND recipient_id=%s AND read_at IS NULL", (tid, u["id"]))
        n = _names(conn, {th["other"]}).get(th["other"], {})
        i_blocked, they_blocked = blocked_between(conn, u["id"], th["other"])
    return {"id": tid, "other": th["other"], "nickname": n.get("nick", "用户"), "verified": bool(n.get("verified")),
            "has_avatar": bool(n.get("has_avatar")),
            "cover": n.get("cover") if n.get("has_profile") else None, "has_profile": bool(n.get("has_profile")),
            "blocked": i_blocked, "banned": bool(n.get("is_banned")),
            "can_send": u["tier"] >= FRIEND_MSG_TIER and not i_blocked and not they_blocked and not n.get("is_banned"),
            "my_tier": u["tier"],
            "messages": [{"id": m["id"], "mine": m["sender_id"] == u["id"], "kind": m["kind"], "body": m["body"],
                          "flagged": m["flagged"], "time": _ts(m["created_at"]), "read": m["read_at"] is not None} for m in msgs]}

@router.get("/api/friends/unread")
def api_unread(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        n = q(conn, """SELECT count(*) AS n FROM c_messages m WHERE m.recipient_id=%s AND m.read_at IS NULL AND NOT m.deleted
                       AND NOT EXISTS (SELECT 1 FROM c_blocks b WHERE b.blocker_id=%s AND b.blocked_id=m.sender_id)""",
              (u["id"], u["id"]), one=True)["n"]
    return {"unread": n}

@router.post("/api/friends/block/{uid}")
def api_block(req: Request, uid: int):
    """拉黑/解除拉黑：拉黑后互相看不到资料，对方无法给你打招呼和发私信"""
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        if uid == u["id"]:
            raise HTTPException(400, "不能拉黑自己")
        if not q(conn, "SELECT id FROM c_users WHERE id=%s", (uid,), one=True):
            raise HTTPException(404, "用户不存在")
        had = q(conn, "DELETE FROM c_blocks WHERE blocker_id=%s AND blocked_id=%s RETURNING blocked_id", (u["id"], uid), one=True)
        if not had:
            q(conn, "INSERT INTO c_blocks(blocker_id,blocked_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (u["id"], uid))
            q(conn, "DELETE FROM c_follows WHERE follower_id=%s AND followee_id=%s", (u["id"], uid))
    return {"blocked": not had}

@router.get("/api/friends/blocks")
def api_blocks(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        ids = [r["blocked_id"] for r in q(conn, "SELECT blocked_id FROM c_blocks WHERE blocker_id=%s ORDER BY created_at DESC", (u["id"],))]
        names = _names(conn, set(ids))
    return {"blocks": [{"uid": i, "nickname": names.get(i, {}).get("nick", "用户")} for i in ids]}

# ---------------- 管理员 ----------------
@router.get("/api/admin/friends")
def api_admin_friends(req: Request, s: str = ""):
    with db() as conn:
        C.need_admin(conn, req)
        s = s.strip()[:60]
        cond, args = "", []
        if s:
            cond = " WHERE u.member_no=%s OR d.nickname ILIKE %s OR u.nickname ILIKE %s"
            like = "%" + s.replace("%", "").replace("_", "") + "%"
            args = [int(s) if s.isdigit() and len(s) < 18 else -1, like, like]
        rows = q(conn, """SELECT d.*, u.verified, u.id_verified, u.is_banned, u.tier, u.member_no, u.nickname AS account_nick,
                 u.buddy_banned_until, u.buddy_ban_reason,
                 (u.avatar IS NOT NULL OR COALESCE(u.is_bot, FALSE)) AS has_avatar,
                 (SELECT count(*) FROM c_reports r WHERE r.target_type='profile' AND r.target_id=d.user_id AND NOT r.resolved) AS reports,
                 (SELECT count(*) FROM c_messages m WHERE m.sender_id=d.user_id AND m.flagged) AS flagged_msgs,
                 (SELECT count(*) FROM c_messages m WHERE m.sender_id=d.user_id AND m.kind='greet' AND m.created_at>now()-interval '1 day') AS greets24
                 FROM c_dating_profiles d JOIN c_users u ON u.id=d.user_id""" + cond +
                 " ORDER BY (d.hidden_by_admin) ASC, reports DESC, d.updated_at DESC LIMIT 50", args)
        photos = {}
        if rows:
            for p in q(conn, "SELECT id,user_id FROM c_dating_photos WHERE user_id = ANY(%s) ORDER BY pos, id", ([r["user_id"] for r in rows],)):
                photos.setdefault(p["user_id"], []).append(p["id"])
    out = []
    for r in rows:
        o = _profile_out(r, photos.get(r["user_id"], []))
        o.update({"account_nick": r["account_nick"], "banned": r["is_banned"], "tier": r["tier"], "reports": r["reports"],
                  "id_verified": bool(r.get("id_verified")), "buddy_banned": bool(r.get("buddy_banned_until")),
                  "flagged_msgs": r["flagged_msgs"], "greets24": r["greets24"]})
        out.append(o)
    return {"profiles": out}

@router.post("/api/admin/friends/{uid}")
async def api_admin_friend_action(req: Request, uid: int):
    csrf(req)
    d = await _json(req)
    act = d.get("action")
    with db() as conn:
        C.need_admin(conn, req)
        if act in ("hide", "unhide"):
            if act == "hide":
                r = q(conn, "UPDATE c_dating_profiles SET hidden_by_admin=TRUE WHERE user_id=%s RETURNING user_id", (uid,), one=True)
            else:
                r = q(conn, "UPDATE c_dating_profiles SET hidden_by_admin=FALSE, auto_hidden=FALSE, report_count=0 WHERE user_id=%s RETURNING user_id", (uid,), one=True)
            if not r:
                raise HTTPException(404, "资料不存在")
            if act == "hide":
                q(conn, "UPDATE c_reports SET resolved=TRUE WHERE target_type='profile' AND target_id=%s", (uid,))
        elif act in ("verify", "unverify"):
            q(conn, "UPDATE c_users SET verified=%s WHERE id=%s", (act == "verify", uid))
        elif act == "delete_photo":
            try:
                pid = int(d.get("photo"))
            except Exception:
                raise HTTPException(400, "照片无效")
            q(conn, "DELETE FROM c_dating_photos WHERE id=%s AND user_id=%s", (pid, uid))
        else:
            raise HTTPException(400, "未知操作")
    return {"ok": True}

@router.get("/api/admin/friend-photo/{pid}")
def api_admin_friend_photo(req: Request, pid: int):
    with db() as conn:
        C.need_admin(conn, req)
        r = q(conn, "SELECT thumb FROM c_dating_photos WHERE id=%s", (pid,), one=True)
    if not r:
        raise HTTPException(404, "照片不存在")
    return Response(bytes(r["thumb"]), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})

@router.get("/api/admin/flagged-messages")
def api_admin_flagged(req: Request):
    with db() as conn:
        C.need_admin(conn, req)
        rows = q(conn, """SELECT m.id, m.body, m.flag_words, m.created_at, m.sender_id, m.recipient_id, m.deleted,
                          s.nickname AS sender, r.nickname AS recipient, s.is_banned
                          FROM c_messages m JOIN c_users s ON s.id=m.sender_id JOIN c_users r ON r.id=m.recipient_id
                          WHERE m.flagged ORDER BY m.id DESC LIMIT 100""")
    return {"messages": [{"id": r["id"], "body": r["body"], "words": r["flag_words"], "time": _ts(r["created_at"]),
                          "sender_id": r["sender_id"], "sender": r["sender"], "recipient_id": r["recipient_id"],
                          "recipient": r["recipient"], "deleted": r["deleted"], "sender_banned": r["is_banned"]} for r in rows]}

@router.delete("/api/admin/messages/{mid}")
def api_admin_del_message(req: Request, mid: int):
    csrf(req)
    with db() as conn:
        C.need_admin(conn, req)
        q(conn, "UPDATE c_messages SET deleted=TRUE WHERE id=%s", (mid,))
        q(conn, "UPDATE c_reports SET resolved=TRUE WHERE target_type='message' AND target_id=%s", (mid,))
    return {"ok": True}
