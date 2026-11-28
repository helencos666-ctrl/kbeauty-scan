# 手机号绑定 + 短信验证码（分支 feat/phone-otp）

开关 `PHONE_OTP_REQUIRED` **默认关闭**：合并/部署后站点行为与以前完全一致（测试 `test/test_phone_otp.py` 的 A 组逐项验证）。
合并时唯一的库改动是「新增」：`c_users.phone_verified_at / phone_verify_method` 两列（可空）、`c_phone_otp`、`c_phone_captcha` 两张空表，均 `IF NOT EXISTS`，不改不删任何现有数据。

## 怎么打开（在 Render 环境变量里设置，然后重启）
1. 先开通短信服务（见下），在 Render 设置对应服务商的环境变量，`SMS_PROVIDER_CN` / `SMS_PROVIDER_KR` 指向它。
2. `PHONE_OTP_REQUIRED=1`。立即生效的是：新注册必须手机号 + 验证码；老账号登录后软提示绑定（三天最多提示一次，随时可点「以后再说」）。
3. 观察一到两周，等老用户陆续绑定。想强制：设 `PHONE_OTP_ENFORCE_FROM=YYYY-MM-DD`（首尔时间）。到期后**只拦写操作**（发帖、评论、私信等），仍能登录、浏览、绑定手机号、改密码、注销账号。不设这个变量 = 永远只是软提示。
4. 紧急回退：把 `PHONE_OTP_REQUIRED` 删掉（或设 0）并重启，立刻回到旧行为；已绑定的手机号保留。

## 环境变量（都不入库）
| 变量 | 作用 |
|---|---|
| `PHONE_OTP_REQUIRED` | 总开关，默认关 |
| `PHONE_OTP_ENFORCE_FROM` | 老账号强制绑定日期，不设则只软提示 |
| `PHONE_OTP_PEPPER` | 验证码哈希的服务端盐（建议设一个随机长字符串） |
| `PHONE_OTP_CAPTCHA` | `simple`（默认，图形验证码）/`turnstile`/`off`/自定义钩子名 |
| `PHONE_OTP_PHONE_DAILY` / `_IP_HOURLY` / `_IP_DAILY` / `_GLOBAL_DAILY` | 每号每天 5、每 IP 每小时 8、每 IP 每天 20、全站每天 1000（成本保险丝，超了返回 503） |
| `SMS_PROVIDER` / `SMS_PROVIDER_CN` / `SMS_PROVIDER_KR` | `mock`（默认）/`aliyun`/`tencent`/`nhn`/`twilio` |
| `ALIYUN_ACCESS_KEY_ID` `ALIYUN_ACCESS_KEY_SECRET` `ALIYUN_SMS_SIGN_NAME` `ALIYUN_SMS_TEMPLATE_CODE` `ALIYUN_SMS_MODE`(pnvs 默认/sms) | 阿里云（仅 +86） |
| `TENCENT_SMS_SECRET_ID` `_SECRET_KEY` `_SDK_APP_ID` `_SIGN_NAME` `_TEMPLATE_ID_CN` `_TEMPLATE_ID_INTL` `_REGION` | 腾讯云（+86 与 +82） |
| `NHN_SMS_APP_KEY` `NHN_SMS_SECRET_KEY` `NHN_SMS_SEND_NO` | NHN Cloud（+82） |
| `TWILIO_ACCOUNT_SID` `TWILIO_AUTH_TOKEN` + `TWILIO_MESSAGING_SERVICE_SID` 或 `TWILIO_FROM` | Twilio（+82，+86 不推荐） |
| `WECHAT_MP_APPID` `WECHAT_MP_SECRET` | 小程序 getPhoneNumber 换号（Render 环境变量，别写进小程序代码） |
| `TURNSTILE_SITE_KEY` `TURNSTILE_SECRET_KEY` | 选 turnstile 时需要，还要放行 CSP（见风险） |

`mock` 服务商：非 Render 环境把验证码打印到服务端日志（本地开发用）；在 Render 上默认**拒绝发送**（返回 503），防止开关打开了却没接短信商。

## 行为一览
- 手机号统一存 E.164（`+8613812345678` / `+821012345678`），页面和接口只显示打码（`+86 138****5678`）。
- 验证码 6 位、5 分钟有效、哈希保存（每条随机盐 + 可选服务端盐）、最多错 5 次后作废、重发冷却 60 秒、新码发出后旧码作废。
- 发送前必须过图形验证码（一次性，答错也作废）；图形验证码之后依次是：每号 60 秒冷却 → 每号每天 → 每 IP 每小时 → 每 IP 每天 → 全站每天，计数全在数据库里，重启/多实例不清零。IP 只存哈希。
- 一个手机号一个账号：`c_users.phone` 已有 UNIQUE 约束；注册/绑定在事务里按手机号加锁并再校验，撞号返回 409。
- 发送失败（服务商报错）不占用户额度，返回通用提示，不泄露服务商信息。
- 新增接口：`GET /api/community/phone/config`、`GET /api/community/phone/captcha`、`POST /api/community/phone/send`、`POST /api/community/phone/bind`、`POST /api/mp/phone/wx-bind`；页面 `/phone-bind`；注册接口在开关打开时多收 `country / phone / phone_code`；登录和 `/api/community/me`、`/api/me/summary` 在开关打开时多返回 `phone: {verified, masked, required_now, enforce_from, days_left}`。
- 小程序：同一后端。小程序请求不带 Cookie，所以绑定接口同时接受 `Authorization: Bearer <会话令牌>`（同一张 c_sessions 表）。小程序改动见 `miniprogram-patch/`（未上传）。目前小程序还没有登录（`/api/mp/login` 未做），绑定页在有登录前不可用。

## 短信服务商选项（价格取自官方页面，抓取日 2026-11-28）
| 方案 | 适用 | 价格 | 对个人的限制 | 来源 |
|---|---|---|---|---|
| 阿里云 号码认证服务「短信认证」(`ALIYUN_SMS_MODE=pnvs`) | +86 | 0.06 元/次起（≤1000 次档），随量降到 0.04；失败不计费；套餐包折合 0.045 起 | **个人实名即可，免企业资质、免签名/模板审核**，用系统赠送签名+模板；只支持中国大陆号码 | https://help.aliyun.com/zh/pnvs/use-cases/sms-verify-for-individual-developers ；价格见 https://help.aliyun.com/zh/sms/product-overview/selection-guide-for-short-message-service-and-sms-verification-service |
| 阿里云 短信服务 SendSms (`mode=sms`) | +86 | 验证码 0.045 元/条（≤10 万条/月，2026-05-20 起新价）；套餐包 1000 条 50 元、5 万条 2250 元（0.045）；国内失败回执不计费 | 需企业资质 + 自建签名/模板；个人只能用「他用」资质+授权委托书（此条来自搜索摘要，未打开原页核实） | https://help.aliyun.com/zh/sms/product-overview/billing-of-messages-sent-to-chinese-mainland |
| 阿里云 国际短信（发往韩国） | +82 | 0.3896 元/条，发送即收费（失败也收） | 本适配器未实现（可用腾讯云/NHN/Twilio） | https://help.aliyun.com/zh/sms/product-overview/pricing-of-messages-sent-to-countries-or-regions-outside-chinese-mainland |
| 腾讯云 国内短信 | +86 | 套餐包：1 万条 470 元（0.047）、10 万条 4500 元（0.045）、50 万条 22500 元；2 年有效；个人认证新开通赠 100 条 | 个人账号做不了运营商签名报备，须提供企业证件+授权委托书；签名报备 7–10 个工作日；验证码模板须含「验证码」，变量最多 6 位数字 | https://cloud.tencent.com/document/product/382/36132 ；https://cloud.tencent.com/document/product/382/13444 ；https://cloud.tencent.com/document/product/382/39023 |
| 腾讯云 国际/港澳台短信（韩国） | +82 | 韩国约 0.034 美元/条（**来自搜索摘要，原价格表未能在线核对，开通前请以控制台为准**）；须先购套餐包（50 元起） | 个人认证可用于验证码/通知 | https://cloud.tencent.com/document/product/382/18051 |
| NHN Cloud SMS | +82 | 9.9 韩元/条（不含 VAT）（二手来源：notifly 2026-08 比价文与代理商页，NHN 官方计算器页未渲染出数字，**待核实**） | **只对企业（사업자）会员开放，个人不能用**；须先做本人认证并登记发信号码（명의자 인증，需通信服务利用证明等） | https://docs.nhncloud.com/ko/Notification/SMS/ko/console-guide/ ；https://blog.notifly.tech/messaging-vendor-comparison/ |
| Twilio | +82（+86 不推荐） | 发往韩国 0.0524 美元/段、发往中国 0.1082 美元/段，失败消息另收 0.001（twilio.com 2026-11 页面） | 个人可注册；发往中国大陆需发件人/模板登记，成功率不稳 | https://www.twilio.com/en-us/sms/pricing/kr ；https://www.twilio.com/en-us/sms/pricing/cn |
| 微信小程序 getPhoneNumber | 小程序内 +86 | 0.03 元/次（成功调用），每个小程序 1000 次体验额度；政府/非营利等免费 | **仅限已完成微信认证的非个人主体小程序**，个人主体报 no permission | https://developers.weixin.qq.com/miniprogram/dev/framework/open-ability/getPhoneNumber |

建议路线（最小成本、不要企业资质时）：+86 先用阿里云「短信认证」（个人可用）；+82 先看用户是否有韩国企业（사업자등록）——有就用 NHN Cloud，没有就用 Twilio 或腾讯云国际短信。按一个月 300 个新注册粗估：+86 约 18 元，+82 约 Twilio $16。以上均为 2026-11-28 抓取的公开价，实际以开通页面为准。

## 用户要做的事（我不会替你注册、付款）
1. 选定服务商并开通（需要你本人实名/付款）。阿里云个人路线：阿里云账号实名 → 号码认证服务控制台开通「短信认证」→ 在「赠送签名」「赠送模板」里记下名称和模板编号 → 建 RAM 子账号只给短信权限，生成 AccessKey。
2. 企业路线（腾讯云/阿里云短信服务/NHN）：准备营业执照；创建签名（报备 7–10 个工作日）；创建验证码模板（含「验证码」二字，变量 {1}=验证码，{2}=有效分钟）；NHN 还要先登记发信号码。
3. 把密钥填到 Render 环境变量（不要发给我或写进聊天、代码）。先用自己的手机号小流量实测：注册、绑定、重发、错码、过期（不要用 mock 在线上测）。
4. 小程序一键绑定：确认小程序主体是否为企业/个体户并已认证；在微信公众平台「付费管理」购买手机号快速验证资源包（可选）；更新《用户隐私保护指引》加入手机号；然后再提审新版本。
5. 隐私政策页补一句：收集手机号用于账号安全与登录（`static/privacy.html`），并核对短信服务商是否会成为数据处理方。
6. 决定强制日期（`PHONE_OTP_ENFORCE_FROM`）。

## 风险与已知限制
- 适配器均**未用真实密钥联调**（无账号、不花钱）；上线前必须用真实号码各测一次。阿里云/腾讯云签名算法有单元测试（自洽），但没有对官方示例向量。
- 老的「未验证手机号账号」仍占着号码：别的账号想绑同一个号会被 409 拒绝（不会抢占）。如果有人冒用别人的号码注册过，真正的主人只能让管理员处理（目前后台没有改号/释放号码的按钮，要手工改库），本期没做自助申诉。
- 注册放宽：开关打开后，注册 IP 限流从 5 次/小时放宽到 15 次/小时（因为运营商 NAT/校园网共用 IP），其余短信额度限制在发送接口。
- 国际短信（韩国/Twilio）按提交即收费，攻击者可刷短信烧钱：已有图形验证码 + 每号/每 IP/全站上限；建议再给服务商控制台设月度预算上限和告警，并把 `PHONE_OTP_GLOBAL_DAILY` 调到合理值。
- 图形验证码对专业打码平台只是减速带；如发现被刷，改用 Turnstile 或阿里云验证码 2.0（`register_captcha_provider` 钩子已留）。Turnstile 需要把 challenges.cloudflare.com 加进 `security.csp()` 的 script-src/frame-src/connect-src，本分支没放行（避免无意放宽 CSP）。
- 中国手机号短信可能被运营商拦截或延迟，海外（韩国）用户收中国号码短信也可能有漫游延迟；页面保留 60 秒冷却与重发。
- 前端静态文件 `login.html` `me.html` 有改动但没有提升缓存版本号（`?v=99`，现有测试要求）；打开开关当天请随部署一起更新缓存版本，避免旧缓存的登录页看不到手机号框。
- 当前没有「更换已验证手机号」「用手机号找回密码」功能（找回密码仍联系管理员）；可作为下一期。
