# kbeauty-push13：同路社区 AI 自动回复（旅韩小助手）

基于线上最新版 push12（community.py / 静态页面）与 push11 的 app.py。无新环境变量；复用已有的 `DEEPSEEK_API_KEY` 与模型 `deepseek-flash`。不要改已有表含义，只追加 `c_users.is_bot`。上传到仓库同路径即可，无需改 app.py。

## 根目录
- community.py
  - 建表时自动创建系统账号「旅韩小助手」（`is_bot`、密码哈希为不可用的 `disabled`、无手机号/邮箱，不能登录；后台不能改此账号）
  - 用户发新帖（已公开）后，后台线程调用 DeepSeek 自动写一条中文评论；发帖接口不等待 AI，失败只记日志
  - 评论默认不回：仅当评论 @旅韩小助手，或帖主在评论里提问时才回
  - 每日全站上限 200 次，单用户每小时 6 次、每天 20 次；超时约 20 秒
  - 后台设置项 `ai_reply`（默认开启），可关闭
  - 系统提示：只提供公开信息参考；签证只说信息不代办；医美、包车保持中立不推荐具体商家；不留联系方式；不提其他社区品牌；不提直播或抖音
  - `/api/community/prices` 仍只返回价格/汇率，不含 AI 开关

## static/
- community-post.html：小助手回复旁显示「AI」标签
- community.css：新增 `.ai-tag`
- admin.html：设置页增加「AI 自动回复」开关
- sw.js：缓存 lvhan-shell-v16 → v17

## test/
- test_community.py：增加 mock DeepSeek 的单元/集成测试（连同原有用例 325/325 通过）；原有用例在管理员登录后先关闭 AI，避免评论计数被后台回复干扰
