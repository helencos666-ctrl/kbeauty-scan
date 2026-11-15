# kbeauty-push16：会员头像 + 搜索引擎优化

基于线上最新版（push14 代码 + push15 已上线的会员页收款码与 sw.js v19）。不要推送部署。不要提及或模仿其他社区品牌。

## 根目录
- community.py
  - 新增 `c_users.avatar BYTEA`（`ADD COLUMN IF NOT EXISTS`，与帖子图片一样存 Postgres，避免 Render 磁盘无持久化）
  - 登录用户可上传/更换/删除自己的头像：`POST/DELETE /api/community/avatar`（需登录和 CSRF）；接受 JPG/PNG/WebP，最大 5MB，服务端用 Pillow 按拍摄方向摆正、居中裁成正方形、缩到 256px、重编码为无 EXIF 的 JPEG
  - 公开读取 `GET /api/community/avatar/{uid}`；无头像返回 404，前端用彩色圆+昵称首字回退
  - 旅韩小助手（系统账号）默认用站点图标作头像，用户和管理员都不能改
  - 后台 `POST /api/admin/users/{uid}/clear_avatar` 可移除不当头像（需管理员登录和 CSRF）
  - 频率限制：每用户每小时 8 次、每 IP 每小时 20 次
  - 后台用户搜索：输入纯数字时按会员编号精确匹配，避免命中邮箱/手机号里的数字片段
  - `/api/community/me`、帖子列表/详情、评论带 `has_avatar` 与 `author_id`
  - 公开帖子页把标题和正文写入 HTML，社区首页把最近帖子标题写入列表，方便搜索引擎抓取
- friends.py
  - 交友卡片、资料页、私信列表和对话返回 `has_avatar`
- seo.py / app.py
  - 每页独特中文标题和描述（含韩国旅游攻略、首尔自由行、韩国景点、韩国交友、旅韩助手等词）
  - canonical、Open Graph / Twitter Card、og 图 `/static/og-cover.jpg`、`lang=zh-CN` 与 hreflang、JSON-LD WebSite + Organization
  - `GOOGLE_SITE_VERIFICATION` / `BAIDU_SITE_VERIFICATION` / `NAVER_SITE_VERIFICATION` 仅在环境变量有值时输出验证标签
  - `/robots.txt` 允许爬虫并指向 sitemap；禁止后台、接口、私信、发帖页和登录页
  - `/sitemap.xml` 列出首页、景点、攻略、社区、公开帖子、约定、会员等公开页及 lastmod
  - IndexNow 密钥文件 `/{key}.txt`（密钥在 `indexnow.key`）
- scripts/ping_indexnow.py
  - 部署后向 api.indexnow.org 提交 sitemap 中的公开网址；不要在开发时运行

## static/
- community.js / community.css：更大的圆形头像（帖子/评论约 42px，资料/顶栏 56px），无图时彩色首字回退；顶栏「设置头像」
- member.html：「我的状态」可设置/更换/删除头像
- community.html、community-post.html、friends.html、friend.html、messages.html、chat.html：展示头像
- admin.html：用户列表显示头像，并提供「移除头像」
- og-cover.jpg：1200×630 社交分享图
- sw.js：缓存 lvhan-shell-v19 → v20
- member.html、pay-wechat.jpg、pay-alipay.jpg：沿用 push15 已上线的收款码（本批未改支付逻辑）

## test/
- test_community.py：头像上传、类型/大小拒绝、管理员移除、默认/回退渲染，以及 robots、sitemap、SEO 标签、IndexNow 密钥文件、公开页服务端正文；sw.js v20
