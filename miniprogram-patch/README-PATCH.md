# 小程序「绑定手机号」改动（未上传，未改动 /workspace/lvhan-miniprogram）

新增文件（复制到小程序项目同名路径）：
- `utils/phone.js`
- `pages/bindphone/bindphone.{js,json,wxml,wxss}`

需要手工改的两处（等后端开关打开、小程序登录做好后再做）：
1. `app.json` 的 `pages` 数组追加 `"pages/bindphone/bindphone"`。
2. `pages/mine/mine.wxml` 里加一行入口（只在 `phoneUnbound` 为真时显示）：
   `<view class="li row" wx:if="{{phoneUnbound}}" data-url="/pages/bindphone/bindphone" bindtap="go"><text class="li-ico">📱</text><text class="flex1">绑定手机号</text><text class="sub">›</text></view>`
   `mine.js` 的 onShow 里请求 `GET /api/me/summary`（带 Authorization: Bearer）后 `this.setData({phoneUnbound: !!(u.phone && !u.phone.verified)})`。

前提：
- 目前小程序没有登录（README 里「后端需要补的接口」第 1 条 /api/mp/login 还没做）。没有 `lh_token` 时，绑定页的所有接口都返回「请先登录」，页面不可用，不影响现有功能。
- 微信一键绑定（getPhoneNumber）只对「已完成微信认证的非个人主体」小程序开放，每次成功调用 0.03 元，每个小程序有 1000 次体验额度（微信官方文档）。个人主体会报 no permission，页面会提示改用短信。
- 小程序后台「服务器域名」里 lvhanzhushou.com 已配置即可，不需要新增域名。
- 隐私保护指引要增加「手机号」收集说明（用于账号安全与登录）。提审前必须补，否则审核可能驳回。
