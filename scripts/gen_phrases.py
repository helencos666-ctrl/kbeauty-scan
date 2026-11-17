"""生成 static/phrases.js（常用句，离线可用）并预生成朗读音频 static/audio/<sha1(韩文)>.mp3。
音频：Microsoft Edge 在线朗读（edge-tts，ko-KR-SunHiNeural，语速 -10%，与 /api/tts 一致），免费、无需密钥。
运行：python scripts/gen_phrases.py
"""
import asyncio, hashlib, json, os
import edge_tts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO = os.path.join(ROOT, "static", "audio")
# 每条：[中文, 韩文, 发音]
SCENES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "phrases.json"), encoding="utf-8"))

async def main():
    os.makedirs(AUDIO, exist_ok=True)
    manifest = {}
    for scene, items in SCENES.items():
        for zh, ko, ro in items:
            h = hashlib.sha1(ko.encode()).hexdigest() + ".mp3"
            p = os.path.join(AUDIO, h)
            if not os.path.exists(p):
                await edge_tts.Communicate(ko, "ko-KR-SunHiNeural", rate="-10%").save(p + ".tmp")
                os.replace(p + ".tmp", p)
            manifest[ko] = h
    js = ("/* 自动生成：scripts/gen_phrases.py，勿手改 */\n"
          "window.LH_SCENES=" + json.dumps(SCENES, ensure_ascii=False, separators=(",", ":")) + ";\n"
          "window.LH_TTS=" + json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + ";\n")
    open(os.path.join(ROOT, "static", "phrases.js"), "w", encoding="utf-8").write(js)
    print("phrases", len(manifest))

asyncio.run(main())
