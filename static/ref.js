/* 旅韩助手 · 设备标识 + 邀请码记录 + 自动绑定 + 首次使用上报（邀请有奖 / 活动邀请）
   设备标识是本机随机数，服务器只存加盐哈希，用于判断「同设备互邀不计入」。
   邀请码同时存 localStorage 和 cookie（微信内置浏览器常清掉其一），7 天内有效；
   只有服务器给出明确结果（成功 / 已绑 / 不符合规则）才清除，网络出错、未登录、活动未开始都保留，下次再试。 */
(function(){
  var DAY = 864e5, CK = 'lh_invc';
  function ls(k, v){ try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch(e){ return null; } }
  function getCookie(){
    var m = document.cookie.match(/(?:^|;\s*)lh_invc=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
  function setCookie(c){
    try { document.cookie = CK + '=' + (c ? encodeURIComponent(c + '.' + Date.now()) : '') + ';path=/;max-age=' + (c ? 7 * 86400 : 0) + ';SameSite=Lax'; } catch(e){}
  }
  function dev(){
    var d = ls('lh_dev');
    if (!d || !/^[a-f0-9]{32}$/.test(d)){
      var a = new Uint8Array(16); (window.crypto || window.msCrypto).getRandomValues(a);
      d = Array.prototype.map.call(a, function(x){ return ('0' + x.toString(16)).slice(-2); }).join('');
      ls('lh_dev', d);
    }
    return d;
  }
  function save(c){ ls('lh_inv', JSON.stringify({c: c, t: Date.now()})); setCookie(c); }
  function clear(){ ls('lh_inv', null); setCookie(''); }
  // 记录邀请码：链接里的 inv 优先（覆盖旧的）
  try {
    var inv = new URLSearchParams(location.search).get('inv');
    if (inv && /^[2-9A-Z]{6}$/i.test(inv)) save(inv.toUpperCase());
  } catch(e){}
  function code(){
    try { var o = JSON.parse(ls('lh_inv') || 'null'); if (o && Date.now() - o.t < 7 * DAY) { if (!getCookie()) setCookie(o.c); return o.c; } } catch(e){}
    // localStorage 被清了：用 cookie 恢复
    var ck = getCookie(), p = ck.split('.');
    if (/^[2-9A-Z]{6}$/.test(p[0] || '') && Date.now() - (+p[1] || 0) < 7 * DAY){ ls('lh_inv', JSON.stringify({c: p[0], t: +p[1] || Date.now()})); return p[0]; }
    return '';
  }
  async function post(url, body){
    var r = await fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-LH': '1', 'X-LH-Dev': dev()}, body: JSON.stringify(body || {})});
    var j = {}; try { j = await r.json(); } catch(e){}
    if (!r.ok) throw new Error(j.detail || '出错了');
    return j;
  }
  // 绑定邀请码。soft=true：没登录时不报错（服务器返回 guest），用于每个页面的自动补绑。
  var inflight = null;
  function bind(soft){
    var c = code(); if (!c) return Promise.resolve(null);
    if (inflight) return inflight;
    inflight = (async function(){
      try {
        var j = await post('/api/ref/bind', {code: c, soft: !!soft});
        if (j.status !== 'inactive' && j.status !== 'guest') clear();
        return j;
      } catch(e){ return null; }
      finally { setTimeout(function(){ inflight = null; }, 3000); }
    })();
    return inflight;
  }
  // 首次使用：查地铁路线 / 收藏优惠。已确认过就不再上报。
  async function use(kind){
    if (ls('lh_ref_done') === '1') return null;
    try {
      var j = await post('/api/ref/use', {kind: kind, code: code()});
      if (j.status === 'guest') return j;
      if (code()) clear();
      if (j.status !== 'none' || !code()) ls('lh_ref_done', '1');
      return j;
    } catch(e){ return null; }
  }
  // 去登录/注册页的链接带上邀请码（微信里 cookie / 存储被清时仍有 URL 兜底）
  document.addEventListener('click', function(ev){
    var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    if (!a) return;
    var h = a.getAttribute('href') || '', c = code();
    if (c && /^\/login(\?|$)/.test(h) && !/[?&]inv=/.test(h)) a.setAttribute('href', h + (h.indexOf('?') < 0 ? '?' : '&') + 'inv=' + c);
  }, true);
  // 任何页面打开时：手里有邀请码就自动补绑（未登录时静默跳过）
  function auto(){ if (code()) bind(true); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', auto); else setTimeout(auto, 0);
  window.LHRef = {dev: dev, code: code, bind: bind, use: use, post: post, clear: clear};
})();
