"""韩国化妆品拍照识别 - 后端代理（密钥只在服务端读取环境变量，不写入日志/前端）"""
import base64, hashlib, io, json, os, re, time, threading, datetime
from collections import defaultdict, deque, OrderedDict
import httpx
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
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

from security import client_ip, SecurityMiddleware  # noqa: E402

GLOBAL_KIND_DAY = {"scan": GLOBAL_DAY, "price": 300, "card": 600, "back": 600, "station": 600, "tts": 1500, "asr": 3000, "asrfix": 1500, "transit": 900, "place": 20000}

def rate_limit(req: Request, kind: str, per_min=PER_MIN, per_day=PER_DAY):
    """按 IP 每分钟/每天限次 + 每种接口全站每日上限（防止换 IP 刷 AI 接口产生费用）。"""
    ip, now = client_ip(req), time.time()
    today = datetime.datetime.now(KST).date().isoformat()
    with _lock:
        if _day["date"] != today:
            _day.update(date=today, count=defaultdict(int), kinds=defaultdict(int)); _day["global"] = 0
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > 60]:
                del _hits[k]
        if len(_hits) > 50000:          # 防止内存被大量不同 IP 撑爆
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > 60]:
                del _hits[k]
        q = _hits[(ip, kind)]
        while q and now - q[0] > 60: q.popleft()
        if len(q) >= per_min:
            raise HTTPException(429, "操作太频繁，请一分钟后再试")
        if _day["count"][(ip, kind)] >= per_day:
            raise HTTPException(429, "今天的使用次数已用完，明天再来")
        kinds = _day.setdefault("kinds", defaultdict(int))
        if kinds[kind] >= GLOBAL_KIND_DAY.get(kind, 500):
            raise HTTPException(429, "今日服务额度已满，请明天再试")
        q.append(now); _day["count"][(ip, kind)] += 1; kinds[kind] += 1
        if kind == "scan": _day["global"] += 1

async def json_body(request: Request) -> dict:
    try:
        d = await request.json()
    except Exception:
        raise HTTPException(400, "请求格式不正确")
    if not isinstance(d, dict):
        raise HTTPException(400, "请求格式不正确")
    return d

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
    return "data:image/jpeg;base64," + base64.b64encode(prep_jpeg(raw)).decode()

def prep_jpeg(raw: bytes) -> bytes:
    """统一压缩成 JPEG 字节（最长边 1024，质量 72），拍照识别与站牌识别共用。"""
    try:
        im = Image.open(io.BytesIO(raw)); im.verify()
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        raise HTTPException(400, "图片无法识别，请换一张 JPG/PNG 照片")
    if max(im.size) > 1024: im.thumbnail((1024, 1024))
    b = io.BytesIO(); im.save(b, "JPEG", quality=72)
    return b.getvalue()

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

from community import router as community_router, ref_middleware
from seo import router as seo_router, html_file_response
from visits import visit_middleware
app.include_router(community_router)
app.include_router(seo_router)
from asr import router as asr_router, start_background_download  # noqa: E402
app.include_router(asr_router)
from route import router as route_router  # noqa: E402
app.include_router(route_router)
from translate import (  # noqa: E402
    SCENES_ZH, HANGUL, translate_text, back_translate as do_back_translate,
    norm_text as _norm_text, lookup_cached, _tc_get, _tc_put,
)
start_background_download()
app.middleware("http")(ref_middleware)
app.middleware("http")(visit_middleware)

# 安全中间件（最外层之一）：安全头、https 跳转、写操作同源校验、请求体流式限长
app.add_middleware(SecurityMiddleware, max_body=MAX_UPLOAD + 100_000,
                   big_paths={"/api/community/posts", "/api/admin/shop-photos"}, big_body=20 * 1024 * 1024)

# ---------- 拍照识别结果缓存（同一张处理后图片不重复调用视觉模型） ----------
SCAN_CACHE_MAX = 60
_scan_cache: "OrderedDict[str, dict]" = OrderedDict()
_scan_lock = threading.Lock()

def _scan_get(h: str):
    with _scan_lock:
        v = _scan_cache.get(h)
        if v is not None:
            _scan_cache.move_to_end(h)
        return v

def _scan_put(h: str, res: dict):
    with _scan_lock:
        _scan_cache[h] = res
        _scan_cache.move_to_end(h)
        while len(_scan_cache) > SCAN_CACHE_MAX:
            _scan_cache.popitem(last=False)

@app.post("/api/scan")
async def scan(request: Request, image: UploadFile = File(...)):
    rate_limit(request, "scan")
    raw = await image.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD: raise HTTPException(413, "图片太大（最大 8MB）")
    if len(raw) < 1000: raise HTTPException(400, "图片太小或为空")
    jpg = prep_jpeg(raw)
    digest = hashlib.sha256(jpg).hexdigest()
    t = time.time()
    hit = _scan_get(digest)
    if hit is not None:
        print(f"scan cache {(time.time()-t)*1000:.0f}ms", flush=True)
        return {"result": hit}
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
    res, usage = await chat([{"role": "user", "content": [
        {"type": "text", "text": SCAN_PROMPT},
        {"type": "image_url", "image_url": {"url": data_url}}]}])
    res = clean_obj(res)
    print(f"scan ok {time.time()-t:.1f}s tokens in={usage.get('prompt_tokens')} out={usage.get('completion_tokens')}", flush=True)
    _scan_put(digest, res)
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

def _trusted_url(u) -> bool:
    """只接受 https 且主机名属于白名单站点（防止 evil.com/?oliveyoung.co.kr 这类伪装）。"""
    try:
        from urllib.parse import urlsplit
        p = urlsplit(str(u))
        host = (p.hostname or "").lower()
    except Exception:
        return False
    return p.scheme == "https" and any(host == d or host.endswith("." + d) for d in TRUSTED)

@app.post("/api/price")
async def price(request: Request):
    rate_limit(request, "price", per_min=5, per_day=30)
    body = await json_body(request)
    q = str(body.get("query", ""))[:80].strip()
    product = str(body.get("product", ""))[:120].strip()
    none = {"found": False, "message": "暂无可靠价格，建议现场比价"}
    if not q: return none
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.post(f"{API}/v1/tools/search", headers={"Authorization": f"Bearer {_key()}"},
                         json={"query": f"{q} 가격", "count": 8, "need_content": True})
    if r.status_code != 200:
        print("search error", r.status_code, flush=True); return none
    results = [x for x in r.json().get("results", []) if _trusted_url(x.get("url", ""))]
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
# 翻译实现见 translate.py（CF Workers AI 免费档；主模型 mistral-small，超时回退 m2m100）

@app.post("/api/card")
async def card(request: Request):
    """旧接口（老版本页面缓存仍可能调用）：一次返回韩文和回译。"""
    rate_limit(request, "card", per_min=10, per_day=80)
    body = await json_body(request)
    zh = _norm_text(body.get("text"))
    if not zh:
        raise HTTPException(400, "请先输入中文")
    res = await translate_text(zh, "zh2ko", scene=str(body.get("scene") or "general"),
                               from_asr=bool(body.get("from_asr")))
    ko = (res.get("ko") or "").strip()
    if not ko:
        raise HTTPException(502, "翻译失败，请重试")
    back = await do_back_translate(ko)
    return {"zh": zh, "ko": ko, "roman": res.get("roman") or "", "back": back.get("zh") or ""}

@app.post("/api/translate")
async def translate(request: Request):
    """dir=zh2ko 返回 ko/roman；dir=ko2zh 返回 zh。回译另走 /api/back。相同句子走缓存。"""
    body = await json_body(request)
    d = "ko2zh" if body.get("dir") == "ko2zh" else "zh2ko"
    scene = body.get("scene") if body.get("scene") in SCENES_ZH else "general"
    text = _norm_text(body.get("text"))
    if not text:
        raise HTTPException(400, "请先输入韩语" if d == "ko2zh" else "请先输入中文")
    if d == "ko2zh" and not HANGUL.search(text):
        raise HTTPException(400, "请输入韩语")
    from_asr = bool(body.get("from_asr"))
    res = lookup_cached(text, d, scene=scene, from_asr=from_asr)
    if not res:
        rate_limit(request, "card", per_min=12, per_day=120)
        res = await translate_text(text, d, scene=scene, from_asr=from_asr)
    if d == "zh2ko":
        ko = (res.get("ko") or "").strip()
        if not ko:
            raise HTTPException(502, "翻译失败，请重试")
        out = {"ko": ko, "roman": (res.get("roman") or "").strip()}
    else:
        zh = (res.get("zh") or "").strip()
        if not zh:
            raise HTTPException(502, "翻译失败，请重试")
        out = {"zh": zh}
    if res.get("cached"):
        out["cached"] = True
    if res.get("engine"):
        out["engine"] = res["engine"]
    if from_asr and res.get("src") and res["src"] != text:
        out["src"] = res["src"]
    return out

@app.post("/api/back")
async def back_translate(request: Request):
    body = await json_body(request)
    ko = _norm_text(body.get("ko"), 700)
    if not ko or not HANGUL.search(ko):
        raise HTTPException(400, "只能回译韩文")
    hit = _tc_get(("back", "", ko, False))
    if hit:
        out = {"zh": (hit.get("zh") or "").strip(), "cached": True}
        return out
    rate_limit(request, "back", per_min=12, per_day=120)
    res = await do_back_translate(ko)
    out = {"zh": (res.get("zh") or "").strip()}
    if res.get("cached"):
        out["cached"] = True
    return out


# ================= 韩语朗读（edge-tts）=================
import hashlib
TTS_DIR = os.path.join(BASE, "tts_cache"); os.makedirs(TTS_DIR, exist_ok=True)

TTS_MAX_FILES = 3000

def _trim_tts_cache():
    """朗读缓存最多保留 TTS_MAX_FILES 个文件，超出时删最旧的，避免磁盘被刷满。"""
    try:
        files = [os.path.join(TTS_DIR, f) for f in os.listdir(TTS_DIR) if f.endswith(".mp3")]
        if len(files) <= TTS_MAX_FILES:
            return
        files.sort(key=lambda f: os.path.getmtime(f))
        for f in files[:len(files) - TTS_MAX_FILES + 100]:
            try: os.remove(f)
            except OSError: pass
    except OSError:
        pass

@app.get("/api/tts")
async def tts(request: Request, text: str = ""):
    text = text.strip()[:700]
    if not text or not HANGUL.search(text): raise HTTPException(400, "只能朗读韩文")
    h = hashlib.sha1(text.encode()).hexdigest()
    pre = os.path.join(BASE, "static", "audio", h + ".mp3")   # 常用句预生成音频
    if os.path.exists(pre):
        return FileResponse(pre, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=2592000"})
    path = os.path.join(TTS_DIR, h + ".mp3")
    if not os.path.exists(path):
        rate_limit(request, "tts", per_min=15, per_day=150)
        _trim_tts_cache()
        import edge_tts
        try:
            await edge_tts.Communicate(text, "ko-KR-SunHiNeural", rate="-10%").save(path + ".tmp")
            os.replace(path + ".tmp", path)
        except Exception:
            raise HTTPException(502, "朗读服务暂不可用")
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=2592000"})

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
    body = await json_body(request)
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

from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402

@app.exception_handler(StarletteHTTPException)
async def starlette_http_exc(request, exc):
    msg = {404: "页面不存在", 405: "请求方式不正确"}.get(exc.status_code, exc.detail if isinstance(exc.detail, str) else "请求无效")
    return JSONResponse({"detail": msg}, status_code=exc.status_code)

@app.exception_handler(RequestValidationError)
async def validation_exc(request, exc):
    # 不回显用户输入和内部字段结构
    return JSONResponse({"detail": "请求参数不正确，请刷新页面重试"}, status_code=422)

@app.exception_handler(Exception)
async def unhandled_exc(request, exc):
    # 只记录异常类型和路径，不记录请求内容、密钥或数据库地址
    print("unhandled error", type(exc).__name__, request.url.path, flush=True)
    return JSONResponse({"detail": "服务器开小差了，请稍后再试"}, status_code=500)

@app.get("/healthz")
def healthz(): return {"ok": True}

@app.get("/")
def index(): return html_file_response("index.html", "/")

@app.get("/attractions")
def attractions():
    return html_file_response("attractions.html", "/attractions")

@app.get("/food")
def food():
    return html_file_response("food.html", "/food")

@app.get("/shopping")
def shopping():
    return html_file_response("shopping.html", "/shopping")

@app.get("/tips")
def tips():
    return html_file_response("tips.html", "/tips")

@app.get("/faq")
def faq():
    return html_file_response("faq.html", "/faq")

@app.get("/beauty")
def beauty():
    return html_file_response("beauty.html", "/beauty")

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

@app.get("/notices")
def notices_redir():
    return RedirectResponse("/community/notices", status_code=302)

@app.get("/privacy")
def privacy():
    return html_file_response("privacy.html", "/privacy")

@app.get("/terms")
def terms():
    return html_file_response("terms.html", "/terms")

@app.get("/static/{name}.html")
def static_html(name: str):
    """静态目录里的 .html 也走页面渲染（加 SEO 标签和脚本 nonce）；其它静态文件仍由 StaticFiles 提供。"""
    if name == "local":
        return RedirectResponse("/trips", status_code=302)
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,40}", name) or not os.path.isfile(os.path.join(BASE, "static", name + ".html")):
        raise HTTPException(404, "Not Found")
    return html_file_response(name + ".html")


from lanjing_seed import router as lanjing_router, seed_once as seed_lanjing  # noqa: E402
from guides_seed import seed_once as seed_guides  # noqa: E402
from official_daily_seed import seed_once as seed_official_daily  # noqa: E402
from hide_shop_posts_v76 import hide_once as hide_shop_posts_v76  # noqa: E402
from hide_shop_posts_v77 import hide_once as hide_shop_posts_v77  # noqa: E402
app.include_router(lanjing_router)
from shop_partners import router as shop_partners_router  # noqa: E402
app.include_router(shop_partners_router)

@app.on_event("startup")
def _startup_seeds():
    seed_lanjing()
    hide_shop_posts_v76()
    hide_shop_posts_v77()
    seed_guides()
    seed_official_daily()

@app.get("/static/subway.json")
def static_subway_json():
    """地铁数据：独立路由加长缓存（mount 之前注册，query string 不影响文件查找）。"""
    return FileResponse(os.path.join(BASE, "static", "subway.json"), media_type="application/json",
                        headers={"Cache-Control": "public, max-age=86400"})

@app.get("/static/stations.json")
def static_stations_json():
    return FileResponse(os.path.join(BASE, "static", "stations.json"), media_type="application/json",
                        headers={"Cache-Control": "public, max-age=86400"})

app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
