# 旅韩助手（Korea Travel Assistant）

面向在韩中国游客的中文小工具（样品）。

- 功能：化妆品拍照识别（含韩国参考价核验）、地铁看图与起终点查路线、中文→韩文沟通卡（按住说话 + edge-tts 韩语朗读）、明洞常用句、韩国免费景点指南（`/attractions`）、美食推荐（`/food`）、旅行攻略（`/tips`）、购物退税（`/shopping`）与买妆说明（`/beauty`），均为静态整理，不含收费接口
- 可安装为 PWA：`manifest.webmanifest` + Service Worker（缓存页面和景点数据，不缓存 `/api/*` 与 POST）
- 后端：FastAPI `app.py`，模型 DeepSeek `deepseek-flash`（看图，关闭思考模式），价格用 DeepSeek Search API，仅收录主流韩国电商且证据须逐字出现在网页正文中
- 防滥用：识别每 IP 5 次/分钟、30 次/天，全站 400 次/天；沟通卡 10/分、80/天；朗读 15/分、150/天；图片 ≤8MB，服务端缩到 1600px，不保存图片
- 本机启动：`./start.sh`（127.0.0.1:8787）；Render 启动命令：`uvicorn app:app --host 0.0.0.0 --port $PORT`（见 render.yaml，免费套餐，DEEPSEEK_API_KEY 只在 Render 环境变量里设置）
