"""韩国化妆品拍照识别 - 后端代理（密钥只在服务端读取环境变量，不写入日志/前端）"""
import base64, io, json, os, re, time, threading, datetime
from collections import defaultdict, deque
import httpx
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

API = "https://api.deepseek.com"
MODEL = "deepseek-flash"
MAX_UPLOAD = 8 * 1024 * 1024          # 8MB
PER_MIN, PER_DAY, GLOBAL_DAY = 5, 30, 400
BASE = os.path.dirname(os.path.abspath(__file__))
KST = datetime.timezone(datetime.timedelta(hours=9))

def _key():
    k = os.environ.get("DEEPSEEK_API_KEY")
    if not k:
        raise HTTPException(503, "服务未配置识别接口")
    return k

# ---------- 限流 ----------
_lock = threading.Lock()
_hits = defaultdict(deque)        # (ip,kind) -> timestamps
_day = {"date": None, "count": defaultdict(int), "global": 0}

def client_ip(req: Request):
    h = req.headers
    xff = (h.get("x-forwarded-for") or "").split(",")[0].strip()
    return h.get("true-client-ip") or h.get("cf-connecting-ip") or xff or (req.client.host if req.client else "?")

def rate_limit(req: Request, kind: str, per_min=PER_MIN, per_day=PER_DAY):
    ip, now = client_ip(req), time.time()
    today = datetime.datetime.now(KST).date().isoformat()
    with _lock:
        if _day["date"] != today:
            _day.update(date=today, count=defaultdict(int)); _day["global"] = 0
        q = _hits[(ip, kind)]
        while q and now - q[0] > 60: q.popleft()
        if len(q) >= per_min:
            raise HTTPException(429, "操作太频繁，请一分钟后再试")
        if _day["count"][(ip, kind)] >= per_day:
            raise HTTPException(429, "今天的识别次数已用完，明天再来")
        if kind == "scan" and _day["global"] >= GLOBAL_DAY:
            raise HTTPException(429, "今日服务额度已满，请明天再试")
        q.append(now); _day["count"][(ip, kind)] += 1
        if kind == "scan": _day["global"] += 1

# ---------- 合规清洗（描述性字段）----------
BANNED = ["美白","增白","祛斑","淡斑","去斑","祛痘","去痘","抗痘","消痘","祛皱","去皱","抗皱","除皱","淡纹","去纹","细纹","皱纹",
          "眼袋","黑眼圈","修复","治疗","消炎","抗炎","抗敏","敏感肌专用","杀菌","抑菌","除菌","药妆","医学","医用","医生推荐",
          "根除","永久","见效","立竿见影","100%","纯天然","0添加","零添加","最好","最佳","第一","顶级","顶尖","天花板","极致",
          "国家级","世界级","全网最低","必买","神器","逆龄","冻龄","换肤","再生","干细胞","激素"]
def clean(s):
    if not isinstance(s, str): return s
    for w in BANNED:
        s = s.replace(w, "（略）")
    s = re.sub(r"(（略）)+", "（略）", s)
    return s

def clean_obj(d, skip=("brand_ko","brand_en","brand_zh","name_ko","name_original","name_zh","package_text","search_query_ko")):
    if isinstance(d, dict):
        return {k: (v if k in skip else clean_obj(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [clean_obj(x) for x in d]
    return clean(d)

SCAN_PROMPT = """你是韩国化妆品识别助手，用户是在韩国的中国游客。请仔细看图片里的化妆品/护肤品包装，只输出一个 JSON 对象，不要多余文字。
严格规则：
1. 只根据包装上看得见的文字和图案判断；看不清就写"包装上未看清"，绝不编造。不确定的品名在 confidence 写"低"。
2. 中文描述里不得写任何功效承诺、绝对化用语、医疗用语（例如美白、祛斑、抗皱、淡纹、修复、治疗、消炎、抗敏、最好、第一、100%等都不要写）。只描述：成分名称、质地、香味、包装标注的原文、通用用法。包装上的功效原文放在 package_text 里原样照抄（韩文/英文），不要翻译成中文功效。
3. 规格（容量/重量）只写包装上实际印着的；没看到就写"包装上未看到"。
4. 不要写价格。
JSON 字段：
{
 "is_cosmetic": true/false,
 "confidence": "高"|"中"|"低",
 "brand_ko": "韩文品牌名（不确定留空）",
 "brand_en": "英文品牌名",
 "brand_zh": "常用中文译名（没有通用译名就音译并注明'音译'）",
 "name_original": "包装上印的产品原名（照抄）",
 "name_ko": "韩文产品名（包装上有就照抄，没有可按品牌官方韩文名写，不确定留空）",
 "name_zh": "中文译名",
 "category": "品类，如 化妆水/精华/乳液/面霜/眼霜/洁面/防晒/面膜/护手霜/口红/粉底 等",
 "package_text": ["包装上能看到的关键原文，最多6条"],
 "features": [{"text":"主要成分或特点（中性描述）","source":"包装"或"常识"}],
 "usage": "通用使用方法（中文，2-3句）",
 "suitable": "适合人群提示（中性，如'包装标注男士用''肤质不确定时建议先在耳后小范围试用'）",
 "size": "规格",
 "search_query_ko": "用于在韩国购物网站搜索这款产品价格的韩文关键词（品牌+品名+规格）",
 "notes": "其他提醒，例如图片模糊、只拍到侧面"
}
如果图片不是化妆品，is_cosmetic=false，其余字段尽量留空，并在 notes 说明。"""

def prep_image(raw: bytes) -> str:
    try:
        im = Image.open(io.BytesIO(raw)); im.verify()
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        raise HTTPException(400, "图片无法识别，请换一张 JPG/PNG 照片")
    if max(im.size) > 1600: im.thumbnail((1600, 1600))
    b = io.BytesIO(); im.save(b, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()

async def chat(messages, max_tokens=1500):
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens,
            "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}, "temperature": 0.2}
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{API}/chat/completions", json=body,
                         headers={"Authorization": f"Bearer {_key()}"})
    if r.status_code != 200:
        print("upstream error", r.status_code, flush=True)   # 不打印返回体与密钥
        raise HTTPException(502, "识别服务暂时不可用，请稍后再试")
    j = r.json()
    txt = j["choices"][0]["message"].get("content") or "{}"
    txt = re.sub(r"^```(json)?|```$", "", txt.strip()).strip()
    try:
        return json.loads(txt), j.get("usage", {})
    except Exception:
        raise HTTPException(502, "识别结果解析失败，请重试")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

@app.middleware("http")
async def limit_body(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_UPLOAD + 100_000:
        return JSONResponse({"detail": "图片太大（最大 8MB）"}, status_code=413)
    return await call_next(request)

@app.post("/api/scan")
async def scan(request: Request, image: UploadFile = File(...)):
    rate_limit(request, "scan")
    raw = await image.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD: raise HTTPException(413, "图片太大（最大 8MB）")
    if len(raw) < 1000: raise HTTPException(400, "图片太小或为空")
    data_url = prep_image(raw)
    t = time.time()
    res, usage = await chat([{"role": "user", "content": [
        {"type": "text", "text": SCAN_PROMPT},
        {"type": "image_url", "image_url": {"url": data_url}}]}])
    res = clean_obj(res)
    print(f"scan ok {time.time()-t:.1f}s tokens in={usage.get('prompt_tokens')} out={usage.get('completion_tokens')}", flush=True)
    return {"result": res}

PRICE_PROMPT = """下面是网页搜索结果（编号、网址、日期、正文片段）。目标产品：{product}。
任务：判断这些结果里有没有明确写着【这一款产品（品牌、品名一致，规格尽量一致）】的韩元售价。
规则：只有正文里明确出现该产品名称附近的价格数字（如 "12,000원"）才算；品牌页里别的产品价格、优惠券金额、运费都不算。不能推测。
输出 JSON：{{"found": true/false, "idx": 结果编号, "price_krw": 整数, "evidence": "从正文中逐字复制的、同时包含产品名和价格的一小段原文（不超过120字）", "spec": "该价格对应规格（正文中有才写）"}}
找不到就 {{"found": false}}。
搜索结果：
{results}"""

TRUSTED = ("oliveyoung.co.kr","coupang.com","shopping.naver.com","smartstore.naver.com","brand.naver.com","ssg.com","lotteon.com",
           "gmarket.co.kr","11st.co.kr","auction.co.kr","amoremall.com","innisfree.com","cosrx.co.kr","lghnh.com","kurly.com",
           "musinsa.com","daisomall.co.kr","lotteimall.com","hmall.com","shinsegae.com","chicor.com")

@app.post("/api/price")
async def price(request: Request):
    rate_limit(request, "price", per_min=5, per_day=30)
    body = await request.json()
    q = str(body.get("query", ""))[:80].strip()
    product = str(body.get("product", ""))[:120].strip()
    none = {"found": False, "message": "暂无可靠价格，建议现场比价"}
    if not q: return none
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.post(f"{API}/v1/tools/search", headers={"Authorization": f"Bearer {_key()}"},
                         json={"query": f"{q} 가격", "count": 8, "need_content": True})
    if r.status_code != 200:
        print("search error", r.status_code, flush=True); return none
    results = [x for x in r.json().get("results", []) if any(d in x.get("url", "") for d in TRUSTED)]
    if not results: return none
    blocks = []
    for i, x in enumerate(results[:6]):
        blocks.append(f"[{i}] {x['url']} 日期:{x.get('date') or '未知'}\n{(x.get('content') or x.get('snippet') or '')[:2500]}")
    res, _ = await chat([{"role": "user", "content": PRICE_PROMPT.format(product=product or q, results="\n\n".join(blocks))}], max_tokens=400)
    try:
        if not res.get("found"): return none
        x = results[int(res["idx"])]
        ev = str(res.get("evidence", "")).strip()
        p = int(res["price_krw"])
        content = (x.get("content") or "") + (x.get("snippet") or "")
        norm = lambda s: re.sub(r"\s+", " ", s)
        # 服务端核验：证据必须逐字出现在网页正文中，且含该价格数字
        if not ev or norm(ev) not in norm(content): return none
        if f"{p:,}" not in ev and str(p) not in ev: return none
        if not (1000 <= p <= 1_000_000): return none
        date = x.get("date")
        date_kst = None; stale = False
        if date:
            try:
                dt = datetime.datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(KST)
                date_kst = dt.strftime("%Y-%m-%d"); stale = (datetime.datetime.now(KST) - dt).days > 180
            except Exception: pass
        return {"found": True, "price_krw": p, "spec": res.get("spec") or "", "evidence": ev[:120],
                "source_url": x["url"], "source_name": x.get("site_name") or x["url"].split("/")[2],
                "page_date": date_kst, "stale": stale,
                "checked_at": datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M") + "（首尔时间）"}
    except Exception:
        return none


# ================= 地铁识别 =================
SUBWAY_PROMPT = """你是首尔/韩国地铁中文向导，用户是中国游客。请看图片（地铁站牌、线路图、换乘标识或出口指示），只输出一个 JSON 对象。
严格规则：
1. 只依据图片里看得见的文字、颜色、编号，加上可靠常识（如首尔地铁4号线是天蓝色、2号线是绿色环线）。看不清或拿不准的就写"图片里看不清"或"无法确定"，绝不编造站名、方向或换乘。
2. 站名给韩文原文 + 中文名（有官方中文标识时照抄，没有就意译/音译并注明）。
3. 每条说明都用简短中文。
JSON 字段：
{
 "is_subway": true/false,
 "confidence": "高"|"中"|"低",
 "sign_type": "站牌/线路图/换乘指示/出口指示/其他",
 "lines": [{"line":"几号线或线路名","color":"颜色","source":"图片"或"常识"}],
 "station_ko": "当前站韩文名", "station_zh": "中文名", "station_code": "站号（图上有才写）",
 "direction": "图上显示的方向，如 往 OO 方向（下一站 OO）",
 "transfer": "图上可见的换乘信息",
 "exits": "图上可见的出口编号及对应地点",
 "visible_text": ["图里能看到的关键原文，最多8条"],
 "advice": ["给游客的分步建议，2-4条，只基于上面能确定的信息"],
 "notes": "看不清或需要现场确认的地方"
}
如果不是地铁相关图片，is_subway=false 并在 notes 说明。"""

@app.post("/api/subway")
async def subway(request: Request, image: UploadFile = File(...)):
    rate_limit(request, "scan")
    raw = await image.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD: raise HTTPException(413, "图片太大（最大 8MB）")
    if len(raw) < 1000: raise HTTPException(400, "图片太小或为空")
    data_url = prep_image(raw)
    t = time.time()
    res, usage = await chat([{"role": "user", "content": [
        {"type": "text", "text": SUBWAY_PROMPT},
        {"type": "image_url", "image_url": {"url": data_url}}]}])
    print(f"subway ok {time.time()-t:.1f}s tokens in={usage.get('prompt_tokens')} out={usage.get('completion_tokens')}", flush=True)
    return {"result": res}

# ================= 沟通卡 =================
CARD_PROMPT = """把下面这句中文翻译成自然、礼貌的韩语（用 해요体/敬语，适合对店员、司机、路人说），只输出 JSON：
{"ko":"韩语句子","roman":"韩语罗马字发音（Revised Romanization，全小写）"}
不要添加原文没有的内容。中文：
"""
BACK_PROMPT = """把下面这句韩语忠实地直译成简体中文，不要润色、不要补充，只输出 JSON：{"zh":"中文"}
韩语：
"""

@app.post("/api/card")
async def card(request: Request):
    rate_limit(request, "card", per_min=10, per_day=80)
    body = await request.json()
    zh = str(body.get("text", "")).strip()[:120]
    if not zh: raise HTTPException(400, "请先输入中文")
    res, _ = await chat([{"role": "user", "content": CARD_PROMPT + zh}], max_tokens=400)
    ko = str(res.get("ko", "")).strip()
    if not ko: raise HTTPException(502, "翻译失败，请重试")
    back, _ = await chat([{"role": "user", "content": BACK_PROMPT + ko}], max_tokens=300)
    return {"zh": zh, "ko": ko, "roman": str(res.get("roman", "")), "back": str(back.get("zh", ""))}

# ================= 韩语朗读（edge-tts）=================
import hashlib
TTS_DIR = os.path.join(BASE, "tts_cache"); os.makedirs(TTS_DIR, exist_ok=True)
HANGUL = re.compile(r"[\uac00-\ud7a3]")

@app.get("/api/tts")
async def tts(request: Request, text: str = ""):
    text = text.strip()[:150]
    if not text or not HANGUL.search(text): raise HTTPException(400, "只能朗读韩文")
    h = hashlib.sha1(text.encode()).hexdigest()
    path = os.path.join(TTS_DIR, h + ".mp3")
    if not os.path.exists(path):
        rate_limit(request, "tts", per_min=15, per_day=150)
        import edge_tts
        try:
            await edge_tts.Communicate(text, "ko-KR-SunHiNeural", rate="-10%").save(path + ".tmp")
            os.replace(path + ".tmp", path)
        except Exception:
            raise HTTPException(502, "朗读服务暂不可用")
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})

# ================= 站名转换（中/英/韩 -> 官方韩文站名）=================
STATION_MAX = 40
STATION_PROMPT = """你是韩国地铁站名转换助手，用户是在韩国的中国游客。
把用户输入（可能是简体中文、英文或韩文，可能不带“站/역”）转换成韩国境内地铁或铁路车站的官方韩文站名。
只输出一个 JSON 对象，不要多余文字。
成功：{"ok": true, "station_ko": "명동역"}
无法确定：{"ok": false, "reason": "无法确定是哪个车站"}
规则：
1. 必须是韩国境内车站；站名一般以“역”结尾（如 명동역、홍대입구역）。
2. 仁川机场用 인천공항1터미널역 / 인천공항2터미널역，金浦机场用 김포공항역。
3. 只返回一个最对应的官方站名，不要附加线路或说明。
4. 拿不准、不是车站、或可能对应多个完全不同的站时，返回 ok=false，不要编造。
用户输入：
"""

@app.post("/api/station")
async def station(request: Request):
    rate_limit(request, "station", per_min=10, per_day=60)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请输入车站名称")
    raw = str(body.get("name", "")).strip()
    if not raw:
        raise HTTPException(400, "请输入车站名称")
    if len(raw) > STATION_MAX:
        raise HTTPException(400, "站名太长，请只输入车站名称")
    res, _ = await chat([{"role": "user", "content": STATION_PROMPT + raw}], max_tokens=200)
    ko = str(res.get("station_ko", "")).strip() if res.get("ok") else ""
    if not ko or not re.search(r"[\uac00-\ud7a3]", ko):
        raise HTTPException(400, str(res.get("reason") or "没能确认这个站名，请换个写法再试"))
    return {"station_ko": ko[:40]}

@app.exception_handler(HTTPException)
async def http_exc(request, exc):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

@app.get("/healthz")
def healthz(): return {"ok": True}

@app.get("/")
def index(): return FileResponse(os.path.join(BASE, "static", "index.html"), headers={"Cache-Control": "no-cache"})

@app.get("/attractions")
def attractions():
    return FileResponse(os.path.join(BASE, "static", "attractions.html"), headers={"Cache-Control": "no-cache"})

@app.get("/food")
def food():
    return FileResponse(os.path.join(BASE, "static", "food.html"), headers={"Cache-Control": "no-cache"})

@app.get("/shopping")
def shopping():
    return FileResponse(os.path.join(BASE, "static", "shopping.html"), headers={"Cache-Control": "no-cache"})

@app.get("/tips")
def tips():
    return FileResponse(os.path.join(BASE, "static", "tips.html"), headers={"Cache-Control": "no-cache"})

@app.get("/beauty")
def beauty():
    return FileResponse(os.path.join(BASE, "static", "beauty.html"), headers={"Cache-Control": "no-cache"})

@app.get("/manifest.webmanifest")
def webmanifest():
    return FileResponse(
        os.path.join(BASE, "static", "manifest.webmanifest"),
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )

@app.get("/sw.js")
def service_worker():
    return FileResponse(
        os.path.join(BASE, "static", "sw.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )

app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
