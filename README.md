# 旅韩助手（Korea Travel Assistant）

面向在韩中国游客的中文小工具（样品）。

- 功能：化妆品拍照识别（含韩国参考价核验）、地铁看图与起终点查路线、中文→韩文沟通卡（按住说话 + edge-tts 韩语朗读）、明洞常用句、韩国景点介绍（`/attractions`）、美食推荐（`/food`）、旅行攻略（`/tips`）、购物退税（`/shopping`）与买妆说明（`/beauty`），均为静态整理，不含收费接口
- 可安装为 PWA：`manifest.webmanifest` + Service Worker（缓存页面和景点数据，不缓存 `/api/*` 与 POST）
- 后端：FastAPI `app.py`，模型 DeepSeek `deepseek-flash`（看图，关闭思考模式），价格用 DeepSeek Search API，仅收录主流韩国电商且证据须逐字出现在网页正文中
- 防滥用：识别每 IP 5 次/分钟、30 次/天，全站 400 次/天；沟通卡 10/分、80/天；朗读 15/分、150/天；图片 ≤8MB，服务端缩到 1600px，不保存图片
- 本机启动：`./start.sh`（127.0.0.1:8787）；Render 启动命令：`uvicorn app:app --host 0.0.0.0 --port $PORT`（见 render.yaml，免费套餐，DEEPSEEK_API_KEY 只在 Render 环境变量里设置）

## 社区（第一期）
- 页面：`/rules` 社区约定、`/community` 列表、`/community/post/{id}` 详情、`/community/new` 发帖、`/login` 登录注册、`/member` 会员说明、`/admin` 管理后台
- 代码：`community.py`（FastAPI router），数据存 Postgres（`DATABASE_URL`），图片压缩后以 bytea 存库（1280px + 480px 缩略图，去除 EXIF 定位）
- 权限（分档）：游客/免费注册可浏览除旅韩交友外的帖子；会员 ¥9.9（tier 1）可看旅韩交友、评论（旅韩交友除外）、点赞、举报；高级会员 ¥19.9（tier 2）可在所有频道发帖并在旅韩交友评论。管理员在后台设置 0/1/2 档
- 访问统计：后台总览显示近 30 天每日 PV/UV 与主要来源（推广码、utm、来路域名）；只统计 HTML 页面 GET，排除静态资源、接口、健康检查和常见爬虫；UV 按首尔日期哈希去重，不保存 IP
- 推广码：`/?ref=CODE` 写入 30 天首次来源 Cookie 并记录访问（同访客同日去重）；注册可选填推广码；后台“推广统计”按日期查看访问/注册/付费/佣金并标记结算；佣金分档（默认会员 ¥3、高级 ¥6），升档只补差额，降档不追回
- 环境变量（只在 Render 里设置，不入库）：`DATABASE_URL`（Render Postgres 的 Internal Database URL）、`ADMIN_PASSWORD`（≥12 位）
- 测试：`python test/test_community.py http://127.0.0.1:8899`
- 商家会员（tier 3）：月付/年付（默认 ¥99/30 天、¥999/365 天），管理员开通/续费（有效期内顺延）；可在商家专区（住宿推荐/包车用车/皮肤管理·医美，键 hotel/car/clinic）发帖，每 30 天 10 篇，超出由管理员按次增加（默认 ¥19.9/篇）；帖子带“商家”标识；推广佣金默认月付 ¥20、年付 ¥200（每次开通/续费各计一笔）
- 普通频道中非商家帖子含商业关键词时进入待审核，后台“审核”通过/拒绝
- 所有价格在后台“价格设置”修改（表 c_settings），无需改代码

## 韩友圈：首页 + 旅韩交友
- 社区首页 `/community`（韩友圈）：汉江青渐变头图 + 分段标签 精选 / 实用干货 / 今日热议 / 刚发布 / 我关注的（接口 tab 值仍为 recommend/news/hot/latest/follow）；“旅韩交友”突出卡片；“按行程找话题”三段（出发前 / 在韩国 / 找商家）频道列表；今日热议、避坑 & 签证新帖、刚发布；右下角“写帖子”按钮
- 新页面：`/community/boards` 全部频道（分组卡片，可“＋ 关注”）、`/community/rate` 汇率换算（汇率在后台“价格与汇率设置”手动填写，无外部接口）、`/community/friends` 旅韩交友、`/community/friends/{id}` 资料详情、`/community/friends/edit` 我的交友资料、`/community/messages` 私信列表、`/community/messages/{id}` 聊天
- 频道（显示名只在 community.py 的 BOARDS 里改，库里存的键不变）：pit 踩坑避雷、group 结伴同行、news 出行提醒、visa 签证问答、study 留学打听、rent 租房找房、used 闲置转让、jobs 工作机会、friends 旅韩交友、hotel 住宿推荐、car 包车用车、clinic 皮肤管理·医美
- 交友代码在 `friends.py`（由 community.py 末尾挂载），权限、打招呼上限（20 次/24 小时）、诈骗关键词等都是文件顶部常量
- 权限：游客/免费只看模糊卡片；会员可看资料和清晰照片；高级会员/商家可发布资料、打招呼、私信
- 安全：拉黑、举报（资料/私信）、对方回复前最多 3 条、私信含转账/投资/虚拟币等词自动提醒并进入后台举报；后台“交友”标签可隐藏资料、删照片、标记“已认证”、处理可疑私信
- 数据库自动迁移（MIGRATE3，幂等）：新增 c_follows、c_board_follows、c_dating_profiles、c_dating_photos、c_threads、c_messages、c_blocks，c_users.verified
