/* 旅韩助手 · 社区通用脚本 */
var LH = (function(){
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  async function api(method, url, body){
    var opt = {method: method, headers: {'X-LH': '1'}, credentials: 'same-origin'};
    if (body instanceof FormData) opt.body = body;
    else if (body !== undefined){ opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    var r, j = {};
    try { r = await fetch(url, opt); } catch(e){ throw new Error('网络不太好，请稍后再试'); }
    try { j = await r.json(); } catch(e){}
    if (!r.ok){ var e = new Error(j.detail || ('出错了（' + r.status + '）')); e.status = r.status; throw e; }
    return j;
  }
  var _me = null;
  async function me(){
    if (_me) return _me;
    _me = await api('GET', '/api/community/me');
    return _me;
  }
  function badge(m){ return m ? '<span class="mbadge">会员</span>' : ''; }
  function showErr(el, msg){ el.textContent = msg; el.style.display = msg ? 'block' : 'none'; }
  function qs(name){ return new URLSearchParams(location.search).get(name); }
  // 浏览器端压缩：最长边 1600px，JPEG 0.82，减少上传流量（服务器还会再压一次）
  function compress(file){
    return new Promise(function(resolve){
      if (!/^image\//.test(file.type) || /gif/.test(file.type)) return resolve(file);
      var url = URL.createObjectURL(file), img = new Image();
      img.onload = function(){
        var max = 1600, w = img.naturalWidth, h = img.naturalHeight, k = Math.min(1, max / Math.max(w, h));
        var c = document.createElement('canvas'); c.width = Math.round(w * k); c.height = Math.round(h * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        c.toBlob(function(b){ resolve(b && b.size < file.size ? new File([b], 'img.jpg', {type: 'image/jpeg'}) : file); }, 'image/jpeg', 0.82);
      };
      img.onerror = function(){ URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }
  async function userbar(el){
    try {
      var d = await me();
      if (d.user){
        el.innerHTML = '<span class="who">👤 ' + esc(d.user.nickname) + badge(d.user.is_member) + '</span>' +
          '<span class="row">' + (d.user.is_member ? '' : '<a class="btn sm primary" href="/member">开通会员 ¥9.9</a>') +
          '<button class="btn sm gray" id="lhLogout">退出</button></span>';
        el.querySelector('#lhLogout').onclick = async function(){ await api('POST', '/api/community/logout', {}); location.reload(); };
      } else {
        el.innerHTML = '<span class="who">游客</span><span class="row"><a class="btn sm primary" href="/login?next=' + encodeURIComponent(location.pathname + location.search) + '">登录 / 注册</a></span>';
      }
      return d;
    } catch(e){
      el.innerHTML = '<span class="who">' + esc(e.message) + '</span>';
      return null;
    }
  }
  async function report(type, id){
    var d = await me().catch(function(){ return {}; });
    if (!d.user){ location.href = '/login?next=' + encodeURIComponent(location.pathname); return; }
    var reason = prompt('请简单说明举报理由（如：广告、诈骗、骚扰、违法违规）');
    if (!reason || !reason.trim()) return;
    try { await api('POST', '/api/community/report', {type: type, id: id, reason: reason.trim()}); alert('已收到举报，管理员会尽快处理'); }
    catch(e){ alert(e.message); }
  }
  return {esc: esc, api: api, me: me, badge: badge, showErr: showErr, qs: qs, compress: compress, userbar: userbar, report: report};
})();
