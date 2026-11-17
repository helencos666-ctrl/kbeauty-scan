# 注册会员一对一私信（设计说明，不上线）

基于线上代码 a47501a（Render 免费 Web + Postgres）。本分支只放效果图和设计，不改线上接口，不部署，不碰生产数据。

## 1. 现状

技术栈：FastAPI + uvicorn，会话 Cookie `lh_sess`（HttpOnly，30 天），密码 scrypt。注册可用邮箱或手机号（手机号不是必填）。用户表 `c_users`：昵称、头像（bytea）、会员档 `tier`（0 普通注册 / 1 会员 / 2 高级会员）、商家有效期、封禁、对外编号 `member_no`。图片和头像都存在 Postgres，避免免费实例磁盘不持久。

站里其实已经有两套一对一会话，不要另起一套：

| 能力 | 表 | 现在谁能用 | 缺口 |
| --- | --- | --- | --- |
| 交友私信 | `c_threads` / `c_messages` / `c_blocks` | 主要从「旅韩交友」打招呼进入；发信要求高级会员（tier 2），且通常要先有交友资料 | 帖子和「我的」没有真正的「私聊」入口（「我的」只有进信箱的入口）；只有文字；聊天页不轮询；没有按条翻译 |
| 商家咨询 | `c_consult_threads` / `c_consult_msgs` | 商家帖/店铺上的「立即咨询」；已支持文字+图片、30 秒未读轮询、拦截站外联系方式 | 这是用户↔商家咨询，不是会员互聊 |

已有能力可直接复用：未读计数（`/api/friends/unread`、`/api/me/summary` 的 `msg_unread`，「我的」私信红点、韩友圈顶栏红点）、拉黑、举报（`c_reports`，后台「举报」和「可疑私信」）、诈骗词标记（转账/投资等：照常送达 + 双方提醒 + 自动进后台）、限流（20 条/分钟、300 条/天、对方回复前最多 3 条）、对方已读、封禁用户不可再发。

会话接口只回昵称、头像、是否认证，不回手机号或邮箱。`/api/community/me` 里自己的账号是掩码（如 `ab***@x.com`），不会出现在对方的会话里。

## 2. 建议方案

会员互聊复用 `c_threads` / `c_messages`（和交友打招呼进同一个信箱），不要新建第二套私信表。商家帖继续走「立即咨询」，与私聊分开。

### 入口

1. 普通帖子、作者主页：作者行增加粉色「✉️ 私聊」按钮（自己的帖不显示）。未登录跳转登录并带上返回地址。点了以后创建或打开两人会话，并在会话顶上留下「来自帖子《标题》」的引用条（只存帖子 id 和标题，不含联系方式）。
2. 「我的」页现有「私信」入口保留，未读红点继续用；信箱页从 `/community/messages` 打开，列表改得松一些（见效果图）。
3. 商家帖 / 店铺：只保留「立即咨询」，不并排再放「私聊」，避免客人不知道该点哪一个。若作者同时是注册会员、对方想以个人身份联系，可到作者主页再私聊（此项待你确认）。

### 谁可以发

建议：只要已登录且未被封禁的注册用户，就可以给其他注册用户发私信（与「注册会员之间互相对话」一致）。交友页的「打招呼」仍可维持高级会员门槛，避免交友列表被骚扰。

防骚扰：沿用「对方回复前最多 3 条」；另加「每天最多新开 10 个会话」；每分钟 20 条、每天 300 条不变。不能给自己、旅韩小助手机器人或已封禁账号发。拉黑后双方都发不了（已有逻辑）。

（若你希望继续「只有付费会员才能发私信」，也可以只把入口加上、权限仍用 tier 门槛，改动更小。）

### 消息类型与界面

- 文字：最多 500 字。
- 图片：一期一起做。复用现有 `process_image`（压缩、去 EXIF/定位，存 bytea+缩略图）。发送栏「＋」选图。图片地址需登录且必须是该会话的参与者才能读。
- 聊天页去掉底部六宫格导航，只留顶栏和输入栏，对话区更松（现网输入栏叠在导航上方，显得挤）。
- 顶栏：返回、头像、昵称、⋯（拉黑 / 举报对方）。引用条可点回原帖。
- 列表：大头像、未读红点、最后一条预览（图片显示「[图片]」）、时间；页顶说明「不会把手机号或邮箱给对方」。
- 已读状态、防骗黄条沿用现逻辑，黄条只在命中敏感词时出现，不占每条消息的空间。

### 传输方式（免费、不新增付费服务）

建议短轮询，不建议 SSE 或 WebSocket：

- 聊天页打开且前台可见时，用 `after=最后一条id` 每 3 秒拉新消息；切到后台时改为 10 秒。
- 「我的」和信箱未读：每 30 秒拉一次（商家咨询已这样用）。
- Render 免费实例是单进程，长连接容易碰到平台超时，也没有 Redis 做多实例推送。以后若升套餐再考虑 SSE。

### 翻译（可选，默认可关）

用现有 Cloudflare Workers AI 免费额度，按条点击「译」，不自动翻全部（省额度）。中↔韩，结果缓存在单独表里，同一条重复点不重复消耗。建议每用户每天 30 次、全站每天设上限；额度用尽时按钮提示「翻译暂时不可用」，原文不受影响。失败则静默回退，不阻塞发送。

### 安全与隐私

- 接口和页面都不返回对方的 phone / email；对外只暴露 `member_no`、昵称、头像。
- 图片鉴权：非会话参与者 404；管理员可在举报详情里查看。
- 敏感词：沿用现有诈骗词表，命中则送达 + 提醒 + 自动进 `c_reports`。
- 站外联系方式（微信、电话、二维码等）：建议与「立即咨询」一样直接拦截（`contact_guard`），沟通留在站内。交友私信目前是允许在信里自行交换的，若你希望私聊可以交换联系方式，可改为「送达并提醒管理员」而不是拒发。此项请确认。
- 不在页面或文案里放微信、群、外链二维码。
- 限流与 CSRF（`X-LH` + Origin 校验）沿用现有安全中间件。
- 管理员可在后台「举报」处理私信和用户举报，「可疑私信」看自动标记，并可软删除消息、封禁账号。增加「举报用户」类型（`c_reports.target_type='user'`）。管理员查看举报详情时可以看到相关会话，不在前台暴露。

## 3. 数据表（只加不删）

已有，继续用：

- `c_threads(id, user_a, user_b, created_at, last_at)`，`UNIQUE(user_a, user_b)`（id 小的在前）
- `c_messages(id, thread_id, sender_id, recipient_id, kind, body, flagged, flag_words, deleted, read_at, created_at)`
- `c_blocks(blocker_id, blocked_id, created_at)`
- `c_reports(target_type, target_id, user_id, reason, resolved, …)`

建议增量迁移（幂等，可重复执行，不改生产已有行的含义）：

```sql
ALTER TABLE c_threads ADD COLUMN IF NOT EXISTS ref_kind TEXT;
ALTER TABLE c_threads ADD COLUMN IF NOT EXISTS ref_id BIGINT;
ALTER TABLE c_threads ADD COLUMN IF NOT EXISTS ref_title TEXT;
ALTER TABLE c_threads ADD COLUMN IF NOT EXISTS last_text TEXT;

ALTER TABLE c_messages ADD COLUMN IF NOT EXISTS img BYTEA;
ALTER TABLE c_messages ADD COLUMN IF NOT EXISTS thumb BYTEA;
ALTER TABLE c_messages ADD COLUMN IF NOT EXISTS width INT;
ALTER TABLE c_messages ADD COLUMN IF NOT EXISTS height INT;

CREATE TABLE IF NOT EXISTS c_message_translations (
  message_id BIGINT NOT NULL REFERENCES c_messages(id) ON DELETE CASCADE,
  dir TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (message_id, dir)
);
```

`kind` 继续用 `greet` / `msg`，新增 `ref`（来自帖子的系统引用）、`img`（有图消息也可 body 带一句说明）。不加未读计数冗余列也可以，现网按 `read_at IS NULL` 计数已有索引 `c_messages_unread_idx`。

## 4. 接口（全部需登录，写操作走 CSRF）

在现有 `/api/friends/threads*` 旁边增加更通用的 `/api/chat/*`（共用同一张表），避免和交友打招呼的门槛缠在一起：

- `POST /api/chat/start`  `{peer_id}` 或 `{post_id}` → `{thread_id}`（已有会话则打开）
- `GET /api/chat/threads`、`GET /api/chat/threads/{tid}?after=`、`POST .../messages`（文字）
- `POST /api/chat/threads/{tid}/images`（multipart）
- `GET /api/chat/img/{mid}`、`?t=1` 缩略图
- `POST /api/chat/messages/{mid}/translate` `{dir: zh2ko|ko2zh}`（可选）
- 未读、拉黑、举报告仍用现有 `/api/friends/unread`、`/api/friends/block/{uid}`、`POST /api/community/report`

发信权限用 `need_user`（若你确认改为付费会员，再改回 `need_tier`）。

## 5. 工作量（方案确认后再做，不含本效果图）

| 项 | 大约 |
| --- | --- |
| 后端：start 入口、权限调整、引用条、迁移 | 1 天 |
| 前端：信箱、会话（轮询、去底栏）、帖子「私聊」按钮、「我的」红点微调 | 1–1.5 天 |
| 图片发送与鉴权读取 | 0.5 天 |
| 可选：按条中韩翻译与额度 | 0.5 天 |
| 后台「举报用户」+ 测试 | 0.5–1 天 |
| **合计** | **约 3.5–5 个工作日**（不做翻译则约 3–4 天） |

不改生产数据。上线前在本机或预览环境跑迁移和测试，经你点头后再部署。

## 6. 效果图

- `mockups/chat-list.html` → `/workspace/lvhan-mockups/chat-list.png`  消息列表
- `mockups/chat-window.html` → `/workspace/lvhan-mockups/chat-window.png`  会话（图片 + 翻译）
- `mockups/chat-entry.html` → `/workspace/lvhan-mockups/chat-entry.png`  帖子上的「私聊」按钮

均为 390×844 逻辑像素、2 倍图（780×1688），与之前「我的 / 优惠」效果图一致。页面比现网列表和聊天略松，会话页去掉底栏。

## 7. 请你确认的问题

1. 所有已登录的注册用户都能互发私信，还是继续只有高级会员能发？建议前者，并用「新会话上限 + 未回复限条」防骚扰。
2. 商家帖是否只保留「立即咨询」，普通帖才出现「私聊」？建议是。
3. 私信里是否禁止留微信、电话、二维码（与咨询一致，沟通留在站内）？建议禁止。若你希望旅伴之间可以交换联系方式，再说，可改成提醒而不拒发。
4. 第一期是否包含发图片，以及每条消息上的中韩「译」按钮？建议图片一起做，翻译可做可关的附加项。
