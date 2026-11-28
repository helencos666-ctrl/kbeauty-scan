/* 旅韩助手 · 手机号验证码控件（PHONE_OTP_REQUIRED 打开时才会被页面使用）
   PhoneOtp.mount(box, {purpose:'register'|'bind'}) → {values(), error(msg), ready}
   依赖 community.js 里的 LH.api / LH.showErr。不含任何密钥。 */
var PhoneOtp = (function(){
  var CSS = '.po-row{display:flex;gap:8px}.po-row select.f{width:112px;flex:none;padding-left:10px;padding-right:6px}.po-row input.f{flex:1;min-width:0}' +
    '.po-cap{display:flex;gap:8px;align-items:center}.po-cap input.f{flex:1;min-width:0}.po-cap img{height:46px;width:132px;border-radius:10px;border:1px solid var(--line);background:#f4f8f7;cursor:pointer;flex:none}' +
    '.po-send{white-space:nowrap;flex:none;min-width:104px}.po-hint{font-size:12px;color:var(--sub);margin-top:6px;line-height:1.6}';
  function el(h){ var d = document.createElement('div'); d.innerHTML = h; return d; }
  function mount(box, opts){
    opts = opts || {};
    var purpose = opts.purpose || 'register';
    if (!document.getElementById('po-css')){ var st = document.createElement('style'); st.id = 'po-css'; st.textContent = CSS; document.head.appendChild(st); }
    box.innerHTML =
      '<label class="f">手机号</label>' +
      '<div class="po-row"><select class="f" id="poCountry" aria-label="国家或地区"><option value="86">🇨🇳 +86</option><option value="82">🇰🇷 +82</option></select>' +
      '<input class="f" id="poPhone" type="tel" inputmode="numeric" autocomplete="tel-national" placeholder="手机号"></div>' +
      '<div id="poCapBox"><label class="f">图形验证码</label><div class="po-cap"><input class="f" id="poCapAns" maxlength="6" inputmode="text" autocomplete="off" placeholder="看图输入"><img id="poCapImg" alt="验证码，点击刷新" title="看不清？点一下换一张"></div></div>' +
      '<label class="f">短信验证码</label>' +
      '<div class="po-row"><input class="f" id="poCode" maxlength="6" inputmode="numeric" autocomplete="one-time-code" placeholder="6 位数字">' +
      '<button type="button" class="btn ghost po-send" id="poSend">获取验证码</button></div>' +
      '<div class="po-hint" id="poHint">验证码 5 分钟内有效。中国 +86 与韩国 +82 手机号都可以。</div>';
    var $ = function(id){ return box.querySelector('#' + id); };
    var cfg = null, capId = '', timer = null, left = 0;
    function err(m){ if (opts.onError) opts.onError(m); }
    function refreshCaptcha(){
      if (!cfg || !cfg.captcha || cfg.captcha.type !== 'simple') return Promise.resolve();
      $('poCapAns').value = '';
      return LH.api('GET', '/api/community/phone/captcha').then(function(r){ capId = r.id; $('poCapImg').src = r.image; }).catch(function(e){ err(e.message); });
    }
    function tick(){
      var b = $('poSend');
      if (left > 0){ b.disabled = true; b.textContent = left + ' 秒后重发'; left--; timer = setTimeout(tick, 1000); }
      else { b.disabled = false; b.textContent = '重新获取'; }
    }
    $('poCapImg').onclick = refreshCaptcha;
    $('poSend').onclick = function(){
      err('');
      var body = {purpose: purpose, country: $('poCountry').value, phone: $('poPhone').value, captcha_id: capId, captcha_answer: $('poCapAns').value};
      if (window.LHCaptchaToken) body.captcha_token = window.LHCaptchaToken();   // 滑块等第三方人机验证的接入点
      if (!body.phone.trim()){ err('请先输入手机号'); return; }
      $('poSend').disabled = true;
      LH.api('POST', '/api/community/phone/send', body).then(function(r){
        $('poHint').textContent = '验证码已发送到 ' + r.masked + '，5 分钟内有效。';
        left = r.cooldown || 60; tick(); $('poCode').focus();
      }).catch(function(e){ err(e.message); $('poSend').disabled = false; })
        .then(function(){ return refreshCaptcha(); });     // 验证码一次性：每次发送后都换新图
    };
    var ready = LH.api('GET', '/api/community/phone/config').then(function(c){
      cfg = c;
      if (!c.captcha || c.captcha.type !== 'simple') $('poCapBox').style.display = 'none';
      return refreshCaptcha().then(function(){ return c; });
    }).catch(function(e){ err(e.message); });
    return {
      ready: ready,
      values: function(){ return {country: $('poCountry').value, phone: $('poPhone').value, code: $('poCode').value}; },
      setPhone: function(country, phone){ if (country) $('poCountry').value = country; if (phone) $('poPhone').value = phone; }
    };
  }
  return {mount: mount};
})();
