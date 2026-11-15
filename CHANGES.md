# kbeauty-push14：会员编号（靓号）可改 + AI 提示补充

基于线上最新版（push12 + push13 覆盖）。不要改 `c_users.id` 主键。不要推送部署。

## 根目录
- community.py
  - 新增 `c_users.member_no BIGINT UNIQUE`（`ADD COLUMN IF NOT EXISTS`，启动时把空值回填为 id）
  - 新注册用户自动分配未被占用的 member_no：默认等于 id；若 id 已被别人当靓号占用，则取其后第一个空号
  - 给用户看的「会员编号」改用 member_no（`/api/community/me`、后台用户列表、按编号搜索、后台帖子/待审核上的编号）
  - 后台新接口 `POST /api/admin/users/{uid}/member_no`（需管理员登录和 CSRF）；正整数、不可占用、不可改旅韩小助手
  - AI 系统提示增加：涉及地铁线路、价格、营业时间等具体信息时，提醒用户出发前再核实
- friends.py
  - 交友资料/个人主页和后台交友搜索按编号改为使用 member_no；内部 uid 仍是主键，关注、私信等接口不变

## static/
- member.html：「我的状态」显示 member_no
- friend.html：个人主页显示会员编号
- admin.html：用户列表编号列改为 member_no，每行增加「改编号」按钮（系统账号除外）；帖子/待审核/交友资料上的编号同步
- sw.js：缓存 lvhan-shell-v17 → v18

## test/
- test_community.py：会员编号分配、改号、搜索、权限/CSRF，以及系统提示与 sw.js v18
