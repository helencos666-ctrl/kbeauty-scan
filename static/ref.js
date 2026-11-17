/* 旅韩助手 · 设备标识 + 邀请码记录 + 首次使用上报（邀请有奖）
   设备标识是本机随机数，服务器只存加盐哈希，用于判断「同设备互邀不计入」。 */
(function(){
  function ls(k, v){ try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch(e){ return null; } }
  function dev(){
    var d = ls('lh_dev');
    if (!d || !/^[a-f0-9]{32}$/.test(d)){
      var a = new Uint8Array(16); (window.crypto || window.msCrypto).getRandomValues(a);
      d = Array.prototype.map.call(a, function(x){ return ('0' + x.toString(16)).slice(-2); }).join('');
      ls('lh_dev', d);
    }
    return d;
  }
  // 记录邀请码（7 天内有效）
  try {
    var inv = new URLSearchParams(location.search).get('inv');
    if (inv && /^[2-9A-Z]{6}$/i.test(inv)) ls('lh_inv', JSON.stringify({c: inv.toUpperCase(), t: Date.now()}));
  } catch(e){}
  function code(){
    try { var o = JSON.parse(ls('lh_inv') || 'null'); if (o && Date.now() - o.t < 7 * 864e5) return o.c; } catch(e){}
    return '';
  }
  async function post(url, body){
    var r = await fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-LH': '1', 'X-LH-Dev': dev()}, body: JSON.stringify(body || {})});
    var j = {}; try { j = await r.json(); } catch(e){}
    if (!r.ok) throw new Error(j.detail || '出错了');
    return j;
  }
  async function bind(){
    var c = code(); if (!c) return null;
    try { var j = await post('/api/ref/bind', {code: c}); if (j.status !== 'inactive') ls('lh_inv', null); return j; } catch(e){ return null; }
  }
  // 首次使用：查地铁路线 / 收藏优惠。已确认过就不再上报。
  async function use(kind){
    if (ls('lh_ref_done') === '1') return null;
    try {
      var j = await post('/api/ref/use', {kind: kind, code: code()});
      if (j.status === 'guest') return j;
      if (code()) ls('lh_inv', null);
      if (j.status !== 'none' || !code()) ls('lh_ref_done', '1');
      return j;
    } catch(e){ return null; }
  }
  window.LHRef = {dev: dev, code: code, bind: bind, use: use, post: post};
})();
