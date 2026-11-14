# 韩国旅行中文助手（样品）
- 功能：化妆品拍照识别（含韩国参考价核验）、地铁看图、中文→韩文沟通卡（edge-tts 韩语朗读 + 浏览器朗读兜底）、明洞常用句（退税/问价/厕所/打车/问路/买药/求助）
- 后端：FastAPI `app.py`，模型 DeepSeek `deepseek-flash`（看图，关闭思考模式），价格用 DeepSeek Search API，仅收录主流韩国电商且证据须逐字出现在网页正文中
- 防滥用：识别每 IP 5 次/分钟、30 次/天，全站 400 次/天；沟通卡 10/分、80/天；朗读 15/分、150/天；图片 ≤8MB，服务端缩到 1600px，不保存图片
- 本机启动：`./start.sh`（127.0.0.1:8787）；Render 启动命令：`uvicorn app:app --host 0.0.0.0 --port $PORT`（见 render.yaml，免费套餐，DEEPSEEK_API_KEY 只在 Render 环境变量里设置）
