/* 手机号绑定接口封装（小程序）。只请求 config.BASE_URL；不打印手机号/验证码/令牌。
   会话令牌 lh_token：等后端 /api/mp/login 做好后由登录流程写入（wx.setStorageSync('lh_token', ...)）。
   没有令牌时所有接口都会返回「请先登录」，页面保持不可用，不影响小程序现有功能。 */
const config = require('../config/index');

function call(path, method, data) {
  return new Promise((resolve, reject) => {
    const token = wx.getStorageSync('lh_token') || '';
    wx.request({
      url: config.BASE_URL + path,
      method: method,
      data: data,
      timeout: 10000,
      header: Object.assign({ 'Content-Type': 'application/json' },
        method !== 'GET' ? { 'X-LH': '1' } : {},
        token ? { Authorization: 'Bearer ' + token } : {}),
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data);
        else reject(new Error((res.data && res.data.detail) || '请求失败（' + res.statusCode + '）'));
      },
      fail() { reject(new Error('网络不可用，请稍后再试')); }
    });
  });
}

module.exports = {
  config: () => call('/api/community/phone/config', 'GET'),
  captcha: () => call('/api/community/phone/captcha', 'GET'),
  sendSms: (p) => call('/api/community/phone/send', 'POST', Object.assign({ purpose: 'bind' }, p)),
  bindSms: (p) => call('/api/community/phone/bind', 'POST', p),
  // getPhoneNumber 按钮回调里的 e.detail.code（5 分钟有效、只能用一次）
  bindWechat: (code) => call('/api/mp/phone/wx-bind', 'POST', { code: code })
};
