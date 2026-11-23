"""找搭子（搭子区）：在「找搭子」频道（board 键名仍为 group，不改库）基础上升级。

- 帖子仍存 c_posts（board='group'），搭子信息（城市/日期/人数/类型/性别要求）存 c_dazi_meta，
  老帖没有 meta 也照常显示，不删不改任何用户数据。
- 个人帖与商家/中介帖分开：tab=personal（默认）只看个人帖；tab=merchant 看商家服务（含商家专区帖）。
- 公开页面不显示站外联系方式：发帖时拦截，列表/详情展示时再兜底打码。
- 举报登录即可（搭子区安全优先），拉黑复用 /api/friends/block/{uid}。
"""
import datetime, re

from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException

from community import (
    csrf, db, q, limit, current_user, need_user, clean_text, ip_of,
    BOARDS, MERCHANT_BOARDS, AUTHOR_COLS, IMG_COLS, _post_out, _with_images, _page,
    exclude_hidden_posts_sql, is_hidden_board,
)

router = APIRouter()
BOARD = "group"
# 搭子区个人帖发帖门槛开关：默认 false = 登录即可发（不要求会员）。
# 以后人多了要改回会员：在 Render 环境变量里设 DAZI_REQUIRE_MEMBER=true 重启即可（或把下面默认值改成 "true"）。
# 只影响搭子区发帖；评论、点赞等以及其他板块的会员规则不变。
def dazi_require_member() -> bool:
    import os
    return (os.environ.get("DAZI_REQUIRE_MEMBER", "false").strip().lower() in ("1", "true", "yes", "on"))

def dazi_post_tier() -> int:
    return 1 if dazi_require_member() else 0

DAZI_DAILY_CAP = 5      # 每人每天（韩国时间）最多发 5 条搭子帖，防刷
KST = datetime.timezone(datetime.timedelta(hours=9))

# 词条全部取自小红书/抖音真实说法（见调研样本），键名存库不要改
CITIES = [("seoul", "首尔"), ("busan", "釜山"), ("jeju", "济州岛"), ("other", "其他")]
KINDS = [("food", "饭搭子·逛吃"), ("visa", "团签拼人"), ("room", "拼房"), ("concert", "演唱会拼车"),
         ("car", "包车拼车·接送机"), ("day", "一日游拼团"), ("local", "在韩搭子"), ("other", "其他")]
GENDERS = [("any", "不限"), ("female", "仅限女生"), ("male", "仅限男生")]
CITY_D, KIND_D, GENDER_D = dict(CITIES), dict(KINDS), dict(GENDERS)
CITY_WORDS = {"seoul": ["首尔", "明洞", "弘大", "江南", "圣水"], "busan": ["釜山"], "jeju": ["济州"]}
# 商家专区老帖没有 meta 时按频道归类
BOARD_KIND = {"car": "car", "hotel": "room", "guide": "day", "kpop": "concert"}
HOT_WORDS = [
    {"w": "首尔搭子", "city": "seoul"},
    {"w": "韩国旅游搭子"},
    {"w": "饭搭子", "kind": "food"},
    {"w": "逛吃搭子", "kind": "food"},
    {"w": "三人团签二缺一", "kind": "visa"},
    {"w": "团签拼人", "kind": "visa"},
    {"w": "拼房", "kind": "room"},
    {"w": "演唱会拼车", "kind": "concert"},
    {"w": "济州岛搭子", "city": "jeju"},
    {"w": "釜山搭子", "city": "busan"},
    {"w": "在韩搭子", "kind": "local"},
    {"w": "长驻首尔搭子", "kind": "local", "city": "seoul"},
    {"w": "一日游拼团", "kind": "day"},
    {"w": "包车拼车", "kind": "car"},
    {"w": "接送机", "kind": "car"},
    {"w": "仅限女生", "gender": "female"},
]
SAFETY_TIPS = [
    "第一次见面约在地铁站、商场、咖啡店等人多的公共场所，告诉家人朋友你的行程。",
    "不提前转账、不交定金；拼房、拼车、拼门票建议见面当场各付各的。",
    "团签拼人先核实对方机票行程和身份，签证请走正规旅行社，别把护照原件交给陌生人。",
    "自称“搭子”却拉你加群、推销包车/代办/医美的，多半是中介引流，可点「举报」。",
    "夜间酒吧、夜店类邀约要格外小心，觉得不对劲随时离开。",
    "所有沟通请用站内私信，不要在公开帖里留微信、电话、二维码。",
]
NOTICE = "仅限结伴出游，不是交友/约会平台；禁止色情、诈骗和金钱往来。"
REPORT_REASONS = ["中介伪装搭子/广告引流", "诈骗或索要钱财", "团签/拼房纠纷", "骚扰或不当言论", "虚假信息", "其他"]

MIGRATE_DAZI = """
CREATE TABLE IF NOT EXISTS c_dazi_meta (
  post_id BIGINT PRIMARY KEY REFERENCES c_posts(id) ON DELETE CASCADE,
  city TEXT NOT NULL DEFAULT 'other',
  kind TEXT NOT NULL DEFAULT 'other',
  date_from DATE,
  date_to DATE,
  people SMALLINT,
  gender TEXT NOT NULL DEFAULT 'any',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_dazi_meta_city_idx ON c_dazi_meta(city, kind);
-- 一次性数据修正（每个 key 只执行一次，之后管理员手动改动不会被覆盖）
CREATE TABLE IF NOT EXISTS c_dazi_once (k TEXT PRIMARY KEY, done_at TIMESTAMPTZ NOT NULL DEFAULT now());
-- 帖子 53：商家账号发的个人找搭子帖，归为个人帖（只改分类字段，不动内容）
WITH m AS (INSERT INTO c_dazi_once(k) VALUES ('post53_personal') ON CONFLICT DO NOTHING RETURNING k)
UPDATE c_posts SET is_merchant=FALSE
 WHERE id=53 AND board='group' AND title='12.1-12号一个人去首尔需要逛街和饭搭子' AND EXISTS (SELECT 1 FROM m);
-- 官方《找搭子使用指南与见面安全须知》在搭子区置顶
WITH m AS (INSERT INTO c_dazi_once(k) VALUES ('pin_dazi_guide') ON CONFLICT DO NOTHING RETURNING k)
UPDATE c_posts SET pinned=TRUE, pinned_at=now()
 WHERE board='group' AND title='找搭子使用指南与见面安全须知' AND NOT deleted
   AND user_id IN (SELECT id FROM c_users WHERE COALESCE(is_official,FALSE)) AND EXISTS (SELECT 1 FROM m);
"""
POST_TYPES = ("personal", "merchant")   # 帖子自身的类型：个人找搭子 / 商家服务

# ---------------- 联系方式打码（展示兜底） ----------------
_KEY = r"(?:微信|微心|威信|薇信|v信|vx|wx|weixin|wechat|qq|kakao(?:talk)?|line|电话|手机|tel|联系)"
_ACC_RE = re.compile(_KEY + r"\s*(?:号|id)?\s*[:：]?\s*([A-Za-z0-9_\-]{5,20})", re.I)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s\-]{8,16}\d)(?!\d)")


def mask_contacts(text: str) -> str:
    if not text:
        return text or ""
    t = _ACC_RE.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + "***", text)
    def ph(m):
        s = m.group(0)
        digits = re.sub(r"\D", "", s)
        if re.fullmatch(r"20\d{6}", digits) or len(digits) < 9:   # 日期等短数字不动
            return s
        return "***"
    return _PHONE_RE.sub(ph, t)


def contact_hits(*texts):
    from contact_guard import find_contacts
    hits = []
    for t in texts:
        for h in find_contacts(t or ""):
            if h not in hits:
                hits.append(h)
    return hits


def _today():
    return datetime.datetime.now(KST).date()


def _parse_date(s, field):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"{field}格式不对")


def meta_out(m, created_at=None):
    if not m:
        return None
    today = _today()
    end = m.get("date_to") or m.get("date_from")
    return {"city": m["city"], "city_name": CITY_D.get(m["city"], "其他"),
            "kind": m["kind"], "kind_name": KIND_D.get(m["kind"], "其他"),
            "date_from": str(m["date_from"]) if m.get("date_from") else None,
            "date_to": str(m["date_to"]) if m.get("date_to") else None,
            "people": m.get("people"), "gender": m.get("gender") or "any",
            "gender_name": GENDER_D.get(m.get("gender") or "any", "不限"),
            "expired": bool(end and end < today)}


def get_meta(conn, pid):
    return q(conn, "SELECT * FROM c_dazi_meta WHERE post_id=%s", (pid,), one=True)


# ---------------- 页面 ----------------
@router.get("/dazi")
def p_dazi():
    return _page("dazi.html", "/dazi")


@router.get("/dazi/new")
def p_dazi_new():
    return _page("dazi.html", "/dazi/new")


@router.get("/api/dazi/options")
def api_options():
    return {"cities": [{"key": k, "name": n} for k, n in CITIES],
            "kinds": [{"key": k, "name": n} for k, n in KINDS],
            "genders": [{"key": k, "name": n} for k, n in GENDERS],
            "hot_words": HOT_WORDS, "safety_tips": SAFETY_TIPS, "notice": NOTICE,
            "report_reasons": REPORT_REASONS, "require_member": dazi_require_member(),
            "daily_cap": DAZI_DAILY_CAP}


@router.post("/api/dazi/check")
async def api_check(req: Request):
    """发帖前预检联系方式（不写库）。前端实时提示，也供线上 E2E 无副作用地验证过滤。"""
    csrf(req)
    limit("dazi-check:" + ip_of(req), 60, 600)
    try:
        d = await req.json()
    except Exception:
        raise HTTPException(400, "请求格式不正确")
    text = str((d or {}).get("text") or "")[:3200]
    hits = contact_hits(text)
    return {"ok": not hits, "hits": hits}


@router.get("/api/dazi/posts")
def api_list(req: Request, tab: str = "personal", city: str = "", kind: str = "", date: str = "",
             gender: str = "", kw: str = "", offset: int = 0, limit_n: int = 20):
    limit_n = max(1, min(limit_n, 30))
    offset = max(0, min(offset, 2000))
    if city and city not in CITY_D:
        raise HTTPException(400, "城市不存在")
    if kind and kind not in KIND_D:
        raise HTTPException(400, "类型不存在")
    if gender and gender not in GENDER_D:
        raise HTTPException(400, "性别要求不存在")
    d = _parse_date(date, "日期")
    kw = (kw or "").strip()[:20]
    with db() as conn:
        u = current_user(conn, req)
        cond, args = ["NOT p.deleted", "p.status='published'", "NOT au.is_banned"], []
        hide_sql, hide_args = exclude_hidden_posts_sql()
        if hide_sql:
            cond.append(hide_sql[5:]); args.extend(hide_args)
        biz_boards = [b for b in MERCHANT_BOARDS if not is_hidden_board(b)]
        if tab == "merchant":
            cond.append("((p.board=%s AND p.is_merchant) OR p.board = ANY(%s))"); args += [BOARD, biz_boards]
        else:
            tab = "personal"
            cond.append("p.board=%s AND NOT p.is_merchant"); args.append(BOARD)
        if u:
            cond.append("NOT EXISTS (SELECT 1 FROM c_blocks b WHERE b.blocker_id=%s AND b.blocked_id=p.user_id)"); args.append(u["id"])
        if city:
            words = CITY_WORDS.get(city, [])
            if words:
                cond.append("(m.city=%s OR (m.post_id IS NULL AND (p.title || ' ' || p.body || ' ' || COALESCE(p.area,'') || ' ' || COALESCE(p.place,'')) ~* %s))")
                args += [city, "|".join(re.escape(w) for w in words)]
            else:
                cond.append("m.city=%s"); args.append(city)
        if kind:
            bks = [b for b, k in BOARD_KIND.items() if k == kind]
            if bks:
                cond.append("(m.kind=%s OR (m.post_id IS NULL AND p.board = ANY(%s)))"); args += [kind, bks]
            else:
                cond.append("m.kind=%s"); args.append(kind)
        if gender:
            cond.append("m.gender=%s"); args.append(gender)
        if d:
            cond.append("m.date_from IS NOT NULL AND m.date_from<=%s AND COALESCE(m.date_to,m.date_from)>=%s"); args += [d, d]
        if kw:
            cond.append("(p.title ILIKE %s OR p.body ILIKE %s)"); args += ["%" + kw + "%"] * 2
        today = _today()
        exp_sql = ("(CASE WHEN m.post_id IS NOT NULL THEN COALESCE(m.date_to,m.date_from) < %s "
                   "ELSE p.created_at < now() - interval '60 days' END)")
        rows = q(conn, f"""SELECT p.*, {AUTHOR_COLS}, (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier,
                 (au.avatar IS NOT NULL OR COALESCE(au.is_bot, FALSE)) AS has_avatar,
                 (au.merchant_until IS NOT NULL AND au.merchant_until>now()) AS au_merchant,
                 COALESCE(au.verified,FALSE) AS au_verified,
                 m.post_id AS m_pid, m.city AS m_city, m.kind AS m_kind, m.date_from AS m_from, m.date_to AS m_to,
                 m.people AS m_people, m.gender AS m_gender,
                 COALESCE({exp_sql}, FALSE) AS is_expired,
                 {IMG_COLS}
                 FROM c_posts p JOIN c_users au ON au.id=p.user_id
                 LEFT JOIN c_dazi_meta m ON m.post_id=p.id
                 WHERE {' AND '.join(cond)}
                 ORDER BY is_expired ASC, COALESCE(p.pinned,FALSE) DESC, p.pinned_at DESC NULLS LAST,
                          COALESCE(au.is_official,FALSE) DESC, p.id DESC
                 OFFSET %s LIMIT %s""", (today, *args, offset, limit_n + 1))
    more = len(rows) > limit_n
    out = []
    for r in rows[:limit_n]:
        o = _with_images(_post_out(r), r)
        o["title"] = mask_contacts(o.get("title") or "")
        o["excerpt"] = mask_contacts(o.get("excerpt") or "")
        if r["m_pid"]:
            o["dazi"] = meta_out({"city": r["m_city"], "kind": r["m_kind"], "date_from": r["m_from"],
                                  "date_to": r["m_to"], "people": r["m_people"], "gender": r["m_gender"]})
        else:
            k = BOARD_KIND.get(r["board"])
            o["dazi"] = {"kind": k, "kind_name": KIND_D.get(k)} if k else None
        o["expired"] = bool(r["is_expired"])
        o["is_biz"] = bool(r.get("is_merchant")) or r["board"] in MERCHANT_BOARDS
        o["biz_verified"] = o["is_biz"] and bool(r["au_merchant"] or r["au_verified"])
        out.append(o)
    return {"tab": tab, "posts": out, "more": more, "next_offset": offset + len(out)}


@router.post("/api/dazi/posts")
async def api_new(req: Request, title: str = Form(""), body: str = Form(""), city: str = Form(""),
                  kind: str = Form(""), date_from: str = Form(""), date_to: str = Form(""),
                  people: str = Form(""), gender: str = Form("any"), post_type: str = Form("personal"),
                  images: list[UploadFile] = File(default=[])):
    csrf(req)
    city = (city or "").strip(); kind = (kind or "").strip(); gender = (gender or "any").strip() or "any"
    if city not in CITY_D:
        raise HTTPException(400, "请选择城市")
    if kind not in KIND_D:
        raise HTTPException(400, "请选择搭子类型")
    if gender not in GENDER_D:
        raise HTTPException(400, "性别要求不对")
    post_type = (post_type or "personal").strip() or "personal"
    if post_type not in POST_TYPES:
        raise HTTPException(400, "请选择发帖类型")
    title = clean_text(title, 40, "标题", 4)
    body = clean_text(body, 3000, "正文", 10)
    df = _parse_date(date_from, "开始日期")
    dt = _parse_date(date_to, "结束日期")
    if not df:
        raise HTTPException(400, "请选择日期")
    if dt and dt < df:
        raise HTTPException(400, "结束日期不能早于开始日期")
    today = _today()
    if (dt or df) < today:
        raise HTTPException(400, "日期已经过去了，请选今天或以后的日期")
    if df > today + datetime.timedelta(days=366) or (dt and (dt - df).days > 90):
        raise HTTPException(400, "日期范围太远或太长（最多一年内、最长 90 天）")
    pn = None
    ps = (people or "").strip()
    if ps:
        if not re.fullmatch(r"\d{1,2}", ps) or not (1 <= int(ps) <= 20):
            raise HTTPException(400, "人数请填 1-20")
        pn = int(ps)
    with db() as conn:
        u = need_user(conn, req)
        if u.get("buddy_banned_until") and u["buddy_banned_until"] > datetime.datetime.now(datetime.timezone.utc):
            raise HTTPException(403, "你的搭子功能暂时被限制，如有疑问请联系管理员")
        if post_type == "merchant" and not u.get("merchant"):
            raise HTTPException(403, "「商家服务」仅限入驻商家发布；个人找搭子请选「个人找搭子」")
        nd = q(conn, """SELECT count(*) AS n FROM c_posts WHERE user_id=%s AND board=%s
                        AND (timezone('Asia/Seoul', created_at))::date = (timezone('Asia/Seoul', now()))::date""",
               (u["id"], BOARD), one=True)["n"]
        if nd >= DAZI_DAILY_CAP:
            raise HTTPException(429, f"每天最多发 {DAZI_DAILY_CAP} 条找搭子，明天再来吧")
        hits = contact_hits(title, body)
        if hits:
            from contact_guard import _log_separately
            _log_separately(u["id"], "搭子区发帖", "、".join(hits), title + "\n" + body)
            raise HTTPException(400, "检测到站外联系方式（%s）。为保护双方安全，公开帖子里请勿留微信、QQ、电话、二维码，"
                                     "想联系的人会通过站内私信找你。" % "、".join(hits))
    imgs = [f for f in (images or []) if f and f.filename]
    if imgs:
        from contact_guard import guard_images
        raws = []
        for f in imgs:
            raws.append(await f.read())
            await f.seek(0)
        with db() as conn:
            guard_images(conn, u["id"], "搭子区图片", raws,
                         msg="公开帖子里请勿放二维码，联系请走站内私信。")
    from community import api_new_post
    req.state.dazi_tier = dazi_post_tier()     # 只对搭子区入口生效
    req.state.dazi_post_type = post_type       # 归类按帖子自身，不按账号
    res = await api_new_post(req, board=BOARD, title=title, body=body, tag="", offer="", valid_until="",
                             merchant_cat="", topics="", area={"seoul": "首尔", "busan": "釜山", "jeju": "济州"}.get(city, ""),
                             place="", show_cat="", spot_name="", spot_area="", avg_price="", images=imgs)
    pid = res.get("id")
    if pid:
        with db() as conn:
            q(conn, """INSERT INTO c_dazi_meta(post_id,city,kind,date_from,date_to,people,gender)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (post_id) DO NOTHING""",
              (pid, city, kind, df, dt, pn, gender))
    return res


@router.get("/api/dazi/meta/{pid}")
def api_meta(pid: int):
    with db() as conn:
        r = q(conn, """SELECT m.*, p.created_at FROM c_dazi_meta m JOIN c_posts p ON p.id=m.post_id
                       WHERE m.post_id=%s AND NOT p.deleted AND p.status='published'""", (pid,), one=True)
    return {"dazi": meta_out(r), "safety_tips": SAFETY_TIPS, "notice": NOTICE}


@router.post("/api/dazi/report")
async def api_report(req: Request):
    """搭子区举报：登录即可（不要求会员），进入后台同一举报队列。"""
    csrf(req)
    try:
        d = await req.json()
    except Exception:
        raise HTTPException(400, "请求格式不正确")
    try:
        pid = int((d or {}).get("post_id"))
    except Exception:
        raise HTTPException(400, "举报对象无效")
    reason = str((d or {}).get("reason") or "").strip()
    if reason not in REPORT_REASONS:
        raise HTTPException(400, "请选择举报理由")
    note = clean_text(str((d or {}).get("note") or ""), 200, "补充说明", 0)
    with db() as conn:
        u = need_user(conn, req)
        limit("dazi-rep:" + str(u["id"]), 10, 3600, "举报太频繁，请稍后再试")
        p = q(conn, "SELECT id,user_id FROM c_posts WHERE id=%s AND NOT deleted", (pid,), one=True)
        if not p:
            raise HTTPException(404, "帖子不存在")
        if p["user_id"] == u["id"]:
            raise HTTPException(400, "不能举报自己的帖子")
        q(conn, "INSERT INTO c_reports(target_type,target_id,user_id,reason) VALUES ('post',%s,%s,%s)",
          (pid, u["id"], ("[搭子区] " + reason + ("：" + note if note else ""))[:200]))
    return {"ok": True, "message": "已收到举报，管理员会尽快处理。遇到诈骗请同时保留聊天记录并报警（112）。"}
