# 旅韩助手（Korea Travel Assistant）

面向在韩中国游客的中文小工具（样品）。

- 功能：化妆品拍照识别（含韩国参考价核验）、地铁看图与起终点查路线、中文→韩文沟通卡（按住说话 + edge-tts 韩语朗读）、明洞常用句、韩国免费景点指南（`/attractions`）、美食推荐（`/food`）、旅行攻略（`/tips`）、购物退税（`/shopping`）与买妆说明（`/beauty`），均为静态整理，不含收费接口
- 可安装为 PWA：`manifest.webmanifest` + Service Worker（缓存页面和景点数据，不缓存 `/api/*` 与 POST）
- 后端：FastAPI `app.py`，模型 DeepSeek `deepseek-flash`（看图，关闭思考模式），价格用 DeepSeek Search API，仅收录主流韩国电商且证据须逐字出现在网页正文中
- 防滥用：识别每 IP 5 次/分钟、30 次/天，全站 400 次/天；沟通卡 10/分、80/天；朗读 15/分、150/天；图片 ≤8MB，服务端缩到 1600px，不保存图片
- 本机启动：`./start.sh`（127.0.0.1:8787）；Render 启动命令：`uvicorn app:app --host 0.0.0.0 --port $PORT`（见 render.yaml，免费套餐，DEEPSEEK_API_KEY 只在 Render 环境变量里设置）

## 社区（第一期）
- 页面：`/community` 列表、`/community/post/{id}` 详情、`/community/new` 发帖、`/login` 登录注册、`/member` 会员说明、`/admin` 管理后台
- 代码：`community.py`（FastAPI router），数据存 Postgres（`DATABASE_URL`），图片压缩后以 bytea 存库（1280px + 480px 缩略图，去除 EXIF 定位）
- 权限：游客可浏览（交友版需登录）；免费注册可点赞、举报；会员（¥9.9，管理员手动开通）可发帖、评论并显示会员标识
- 推广码：`/?ref=CODE` 写入 30 天首次来源 Cookie 并记录访问（同访客同日去重）；注册可选填推广码；后台“推广统计”按日期查看访问/注册/付费/佣金并标记结算
- 环境变量（只在 Render 里设置，不入库）：`DATABASE_URL`（Render Postgres 的 Internal Database URL）、`ADMIN_PASSWORD`（≥12 位）
- 测试：`python test/test_community.py http://127.0.0.1:8899`
