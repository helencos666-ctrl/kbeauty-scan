const phone = require('../../utils/phone');

Page({
  data: {
    countries: [{ code: '86', name: '中国 +86' }, { code: '82', name: '韩国 +82' }],
    ci: 0, phone: '', code: '', capId: '', capImg: '', capAns: '',
    left: 0, sending: false, binding: false, err: '', done: false, maskedNo: '',
    wechatOk: false, force: false, next: ''
  },
  onLoad(q) {
    this.setData({ next: q && q.next ? decodeURIComponent(q.next) : '' });
    phone.config().then((c) => {
      if (!c.enabled) { wx.showToast({ title: '暂未开放', icon: 'none' }); return; }
      this.setData({ wechatOk: !!c.wechat, force: !!c.enforce_active });
      if (c.captcha && c.captcha.type === 'simple') this.loadCaptcha();
    }).catch((e) => this.setData({ err: e.message }));
  },
  onUnload() { if (this._t) clearTimeout(this._t); },
  loadCaptcha() {
    phone.captcha().then((r) => this.setData({ capId: r.id, capImg: r.image, capAns: '' })).catch(() => {});
  },
  onCountry(e) { this.setData({ ci: Number(e.detail.value) }); },
  onPhone(e) { this.setData({ phone: e.detail.value }); },
  onCode(e) { this.setData({ code: e.detail.value }); },
  onCapAns(e) { this.setData({ capAns: e.detail.value }); },
  tick() {
    if (this.data.left <= 0) return;
    this.setData({ left: this.data.left - 1 });
    this._t = setTimeout(() => this.tick(), 1000);
  },
  send() {
    const d = this.data;
    if (!d.phone) { this.setData({ err: '请先输入手机号' }); return; }
    this.setData({ sending: true, err: '' });
    phone.sendSms({ country: d.countries[d.ci].code, phone: d.phone, captcha_id: d.capId, captcha_answer: d.capAns })
      .then((r) => { this.setData({ left: r.cooldown || 60 }); this.tick(); })
      .catch((e) => this.setData({ err: e.message }))
      .then(() => { this.setData({ sending: false }); if (d.capImg) this.loadCaptcha(); });   // 图形验证码一次性
  },
  bind() {
    const d = this.data;
    if (!d.phone || !d.code) { this.setData({ err: '请输入手机号和验证码' }); return; }
    this.setData({ binding: true, err: '' });
    phone.bindSms({ country: d.countries[d.ci].code, phone: d.phone, code: d.code })
      .then((r) => this.setData({ done: true, maskedNo: r.phone }))
      .catch((e) => this.setData({ err: e.message }))
      .then(() => this.setData({ binding: false }));
  },
  onWxPhone(e) {
    // 用户拒绝授权 / 额度用完（errno 1400001）/ 个人主体无权限时 code 为空，提示改用短信
    const code = e.detail && e.detail.code;
    if (!code) { this.setData({ err: '没有拿到微信手机号，请改用下面的短信验证码' }); return; }
    phone.bindWechat(code)
      .then((r) => this.setData({ done: true, maskedNo: r.phone }))
      .catch((er) => this.setData({ err: er.message }));
  },
  goBack() {
    if (this.data.next) wx.redirectTo({ url: this.data.next, fail: () => wx.switchTab({ url: '/pages/index/index' }) });
    else wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/index/index' }) });
  }
});
