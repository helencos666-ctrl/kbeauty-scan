# kbeauty-push17：安全加固 + 隐私政策/用户协议

基于线上 93d109c（push16）。不要自动部署；上传后手动 trigger deploy。

## 新增
- security.py：安全中间件
  - 安全响应头：CSP（脚本 nonce，每次请求随机）、HSTS、X-Content-Type-Options、X-Frame-Options、Referrer-Policy、Permissions-Policy、COOP；/api 响应 no-store
  - 经代理的 http 请求 301 跳到 https
  - 写操作（POST/PUT/PATCH/DELETE）校验 Origin/Referer 必须是本站（在 X-LH 请求头之外再加一层防 CSRF）
  - 请求体流式限长（分块上传也会截断）
  - 客户端 IP 只信任 Cloudflare 的 CF-Connecting-IP（原来先信任可伪造的 True-Client-IP / X-Forwarded-For 第一个值，限流可被绕过）
- static/privacy.html（/privacy）隐私政策、static/terms.html（/terms）用户协议与免责声明
- 自助注销账号：POST /api/community/account/delete（需登录、CSRF、密码和“注销”确认），会员页「我的状态」底部入口
- 后台用户操作「彻底删除账号」（处理删除请求）
- test/test_security.py：70 项安全测试

## 修复
- 【高】存储型 XSS：帖子标题/正文/昵称写进 JSON-LD 时没转义，标题含 </script> 可注入脚本。现转义 < > & 等
- 【中】帖子标题含反斜杠（如 \1）时帖子页 500：正则替换改用函数
- 【中】未登录也能上传大图让服务器解码：发帖、头像、交友照片先验身份再处理图片；图片只启用 JPEG/PNG/WebP/GIF/BMP 解码器，先查像素再解码（防解压炸弹）
- 【中】AI 接口（翻译卡/站名/比价/朗读）只有按 IP 的限额：增加每种接口全站每日上限；朗读缓存最多 3000 个文件
- 后台登录：按失败次数锁定（同 IP 5 次/15 分钟、全站 20 次/小时），失败延迟 0.5 秒，不再提示密码长度要求
- 注册限流：每 IP 每小时 5 次、每天 10 次，全站每小时 300 次
- 比价来源网址按主机名白名单校验（防 evil.com/?oliveyoung.co.kr）
- 推广访问的访客标识改为带密钥的哈希（不能靠穷举 IP 反推）
- JSON 请求体不是对象时返回 400（原来 500）；参数校验错误、未处理异常只返回通用中文提示，不回显输入
- AI 回复线程列表定期清理
- 依赖：requirements.txt 锁定全部间接依赖版本；pip-audit 无已知漏洞
- community.js：去掉内联 onerror（CSP 兼容）
- 页面底部加「用户协议 · 隐私政策 · 社区约定」和“仅供参考、不是旅行社、不代办签证”；注册勾选改为同意三份文件和境外存储
- sitemap / seo 加 /privacy、/terms；sw.js 缓存 lvhan-shell-v20 → v21
- scripts/ping_indexnow.py：修复 url 列表 bug
