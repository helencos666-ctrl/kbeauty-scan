"""语音识别（录音上传 → 服务器转文字）。

用于 iPhone Safari / 微信内置浏览器等不支持或不稳定的网页语音识别环境。
- 首选：Cloudflare Workers AI Whisper（免费额度；需要环境变量 CF_ACCOUNT_ID + CF_AI_TOKEN，未配置则跳过）
- 兜底：本机开源模型 sherpa-onnx Paraformer-zh-small（int8，约 82MB，仅中文；免费、不出网）
录音只在内存里处理，不保存到磁盘、不写日志。
"""
import asyncio, base64, hashlib, io, os, threading, time, wave, logging
import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

log = logging.getLogger("asr")
router = APIRouter()

MAX_AUDIO = 2 * 1024 * 1024          # 上传最大 2MB（30 秒 aac/opus 通常 < 300KB）
MAX_SEC = 30.0
SR = 16000

CF_ACCOUNT = os.getenv("CF_ACCOUNT_ID", "").replace("\ufeff", "").strip()
CF_TOKEN = os.getenv("CF_AI_TOKEN", "").replace("\ufeff", "").strip()
CF_MODEL = os.getenv("CF_ASR_MODEL", "@cf/openai/whisper-large-v3-turbo")

MODEL_NAME = "sherpa-onnx-paraformer-zh-small-2024-03-09"
MODEL_DIR = os.getenv("ASR_MODEL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "asr_models", MODEL_NAME))
FILES = {  # 文件名 -> sha256（防止下载被篡改）
    "model.int8.onnx": "3ef6c19369b912f7caf3cef8e545c5ccd1a33d9d7ec792a46668dc41c4b229ec",
    "tokens.txt": "4b2d964e18b9cf139b473003b6698fb2ed9a2a5ec55b93daa677b28f578897aa",
}
HF = "https://huggingface.co/csukuangfj/" + MODEL_NAME + "/resolve/main/"

_dl_lock = threading.Lock()
_load_lock = threading.Lock()
_run_lock = threading.Lock()   # 免费实例 CPU 很少，同一时间只跑一个识别
_rec = None
_state = {"status": "idle", "err": ""}

# 常见地名同音纠错（只替换明确的错误写法）
FIX = {"民洞": "明洞", "名洞": "明洞", "鸣洞": "明洞", "洪大": "弘大", "红大": "弘大", "东大们": "东大门",
       "景福功": "景福宫", "南山塔": "南山塔", "仁寺东": "仁寺洞", "人寺洞": "仁寺洞", "江难": "江南"}


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _have_model():
    return all(os.path.exists(os.path.join(MODEL_DIR, n)) for n in FILES)


def ensure_model():
    """下载并校验本地模型（只在缺失时下载）。"""
    with _dl_lock:
        if _have_model():
            return True
        os.makedirs(MODEL_DIR, exist_ok=True)
        _state["status"] = "downloading"
        try:
            with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, connect=15)) as c:
                for name, sha in FILES.items():
                    dst = os.path.join(MODEL_DIR, name)
                    if os.path.exists(dst):
                        continue
                    tmp = dst + ".part"
                    with c.stream("GET", HF + name) as r:
                        r.raise_for_status()
                        with open(tmp, "wb") as f:
                            for chunk in r.iter_bytes(1 << 20):
                                f.write(chunk)
                    if _sha(tmp) != sha:
                        os.remove(tmp)
                        raise RuntimeError("模型校验失败: " + name)
                    os.replace(tmp, dst)
            _state["status"] = "downloaded"
            return True
        except Exception as e:  # noqa: BLE001
            _state.update(status="error", err=str(e)[:200])
            log.warning("asr model download failed: %s", e)
            return False


def _load():
    global _rec
    if _rec is not None:
        return _rec
    with _load_lock:
        if _rec is not None:
            return _rec
        if not ensure_model():
            return None
        try:
            import sherpa_onnx
            _state["status"] = "loading"
            _rec = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=os.path.join(MODEL_DIR, "model.int8.onnx"),
                tokens=os.path.join(MODEL_DIR, "tokens.txt"), num_threads=1)
            _state["status"] = "ready"
        except Exception as e:  # noqa: BLE001
            _state.update(status="error", err=str(e)[:200])
            log.warning("asr model load failed: %s", e)
            return None
    return _rec


def warm_async():
    if _rec is None and _state["status"] not in ("downloading", "loading"):
        threading.Thread(target=_load, daemon=True).start()


def decode_audio(raw: bytes):
    """任意格式（iPhone 的 mp4/aac、安卓的 webm/opus、ogg、wav）→ 16kHz 单声道 float32。"""
    import av, numpy as np
    try:
        c = av.open(io.BytesIO(raw))
    except Exception:
        raise HTTPException(400, "录音格式无法识别，请重试")
    out, n = [], 0
    try:
        rs = av.AudioResampler(format="s16", layout="mono", rate=SR)
        for fr in c.decode(audio=0):
            for g in rs.resample(fr):
                a = g.to_ndarray().reshape(-1); out.append(a); n += len(a)
            if n > SR * MAX_SEC:
                break
        for g in rs.resample(None):
            out.append(g.to_ndarray().reshape(-1))
    except HTTPException:
        raise
    except Exception:
        if not out:
            raise HTTPException(400, "录音格式无法识别，请重试")
    finally:
        c.close()
    if not out:
        return np.zeros(0, dtype="float32")
    return (np.concatenate(out)[: int(SR * MAX_SEC)].astype("float32") / 32768.0)


def _wav_bytes(pcm):
    import numpy as np
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
    return b.getvalue()


async def _cf(pcm, lang):
    body = {"audio": base64.b64encode(_wav_bytes(pcm)).decode(), "task": "transcribe", "language": lang, "vad_filter": True}
    if lang == "zh":
        body["initial_prompt"] = "以下是普通话的句子，使用简体中文。"
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{CF_MODEL}"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, json=body, headers={"Authorization": "Bearer " + CF_TOKEN})
    if r.status_code != 200:
        raise RuntimeError("cf %s" % r.status_code)
    j = r.json()
    if not j.get("success"):
        raise RuntimeError("cf fail")
    return (j.get("result") or {}).get("text", "").strip()


def _local(pcm):
    rec = _load()
    if rec is None:
        raise HTTPException(503, "语音识别正在准备，请几秒后再试")
    with _run_lock:
        s = rec.create_stream()
        s.accept_waveform(SR, pcm)
        rec.decode_stream(s)
        return s.result.text.strip()


def _fix(t: str) -> str:
    for a, b in FIX.items():
        t = t.replace(a, b)
    return t


@router.post("/api/asr/warm")
async def asr_warm():
    if not (CF_ACCOUNT and CF_TOKEN):
        warm_async()
    return {"ok": True, "engine": "cf" if CF_ACCOUNT and CF_TOKEN else "local", "status": _state["status"],
            "ko": bool(CF_ACCOUNT and CF_TOKEN)}


@router.post("/api/asr")
async def asr(request: Request, lang: str = "zh"):
    from app import rate_limit
    lang = "ko" if lang == "ko" else "zh"
    rate_limit(request, "asr", per_min=15, per_day=300)
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_AUDIO:
            raise HTTPException(413, "录音太长，请分段说（每次 30 秒内）")
    if len(raw) < 200:
        raise HTTPException(400, "没有录到声音，请按住说话")
    t0 = time.time()
    pcm = await run_in_threadpool(decode_audio, bytes(raw))
    dur = len(pcm) / SR
    if dur < 0.3:
        return {"ok": True, "text": "", "engine": "none"}
    import numpy as np
    if float(np.sqrt(np.mean(pcm * pcm))) < 0.002:   # 几乎静音（麦克风没声音）
        return {"ok": True, "text": "", "engine": "silent"}
    text, engine = "", ""
    if CF_ACCOUNT and CF_TOKEN:
        try:
            text, engine = await _cf(pcm, lang), "cf"
        except Exception as e:  # noqa: BLE001
            log.warning("asr cf failed: %s", e)
    if not engine:
        if lang == "ko":
            raise HTTPException(503, "韩语语音识别暂未开通，请点输入框用键盘上的话筒说韩语")
        text, engine = await run_in_threadpool(_local, pcm), "local"
    if lang == "zh":
        text = _fix(text)
    return {"ok": True, "text": text[:600], "engine": engine, "sec": round(dur, 1), "ms": int((time.time() - t0) * 1000)}


def start_background_download():
    """启动后在后台预先下载模型（不加载，避免占内存/CPU）。"""
    if CF_ACCOUNT and CF_TOKEN:
        return
    threading.Thread(target=ensure_model, daemon=True).start()
