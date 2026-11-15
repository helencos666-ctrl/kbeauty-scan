/* 旅韩助手 · 社区通用脚本 */
/* 头像加载失败时回退到首字母（替代内联 onerror，配合内容安全策略 CSP） */
document.addEventListener('error', function(ev){
  var t = ev.target;
  if (t && t.tagName === 'IMG' && t.getAttribute('data-avfb') === '1'){
    t.style.display = 'none';
    if (t.parentNode && t.parentNode.classList) t.parentNode.classList.add('fb');
  }
}, true);
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
  function badge(t){ t = +t || 0; return t >= 3 ? '<span class="mbadge biz">商家</span>' : (t === 2 ? '<span class="mbadge pro">高级会员</span>' : (t === 1 ? '<span class="mbadge">会员</span>' : '')); }
  var _pr = null;
  // 价格由管理员在后台设置，页面里 data-p="price_xxx" 的元素自动填入
  async function prices(){
    if (!_pr){ try { _pr = await api('GET', '/api/community/prices'); } catch(e){ return null; } }
    document.querySelectorAll('[data-p]').forEach(function(el){ if (_pr[el.dataset.p] != null) el.textContent = _pr[el.dataset.p]; });
    return _pr;
  }
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
        el.innerHTML = '<span class="who">' + avatar(d.user.id, d.user.nickname, d.user.has_avatar, 'lg') +
          '<span>' + esc(d.user.nickname) + badge(d.user.display_tier) + '</span></span>' +
          '<span class="row"><button class="btn sm ghost" type="button" id="lhAv">设置头像</button>' +
          '<a class="btn sm ghost" href="/community/messages" style="position:relative">✉️ 私信' + (d.user.unread ? '<span class="dot">' + (d.user.unread > 99 ? '99+' : d.user.unread) + '</span>' : '') + '</a>' + (d.user.tier >= 2 ? '' : '<a class="btn sm primary" href="/member">' + (d.user.tier === 1 ? '升级高级会员' : '开通会员') + '</a>') +
          '<button class="btn sm gray" id="lhLogout">退出</button></span>';
        el.querySelector('#lhAv').onclick = function(){ pickAvatar(function(){ _me = null; userbar(el); }); };
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
    if (d.user.tier < 1){ if (confirm('开通会员后才能举报。去了解会员？')) location.href = '/member'; return; }
    var reason = prompt('请简单说明举报理由（如：广告、诈骗、骚扰、违法违规）');
    if (!reason || !reason.trim()) return;
    try { await api('POST', '/api/community/report', {type: type, id: id, reason: reason.trim()}); alert('已收到举报，管理员会尽快处理'); }
    catch(e){ alert(e.message); }
  }
  function vb(v){ return v ? '<span class="vbadge">✔ 已认证</span>' : ''; }
  function initial(nick){ var s = String(nick || '').trim(); return s ? s.charAt(0) : '?'; }
  function avColor(s){
    var h = 0, t = String(s || '');
    for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0;
    return ['#e86b8a','#5b7fd6','#2f9e7a','#d4a017','#8e6bb8','#e07a5f','#4a9aa3','#c06c84'][h % 8];
  }
  // size: sm 42px（帖子/评论） / md 48px（私信列表） / lg 56px（资料/顶栏）
  // has 为真时用社区头像；否则用 fallbackUrl（如交友封面）或彩色首字
  function avatar(uid, nick, has, size, fallbackUrl){
    size = size || 'sm';
    var src = (has && uid) ? ('/api/community/avatar/' + uid) : (fallbackUrl || '');
    var img = src ? '<img src="' + esc(src) + '" alt="" data-avfb="1">' : '';
    return '<span class="av av-' + size + (src ? '' : ' fb') + '" style="background:' + avColor(uid || nick) + '">' +
      img + '<span class="av-letter">' + esc(initial(nick)) + '</span></span>';
  }
  function pickAvatar(onOk){
    var inp = document.createElement('input');
    inp.type = 'file'; inp.accept = 'image/jpeg,image/png,image/webp';
    inp.onchange = async function(){
      var f = inp.files && inp.files[0]; if (!f) return;
      if (f.size > 5 * 1024 * 1024){ alert('头像不能超过 5MB，请换一张'); return; }
      try {
        var fd = new FormData(); fd.append('image', f);
        await api('POST', '/api/community/avatar', fd);
        _me = null;
        if (onOk) onOk(); else location.reload();
      } catch(e){ alert(e.message); }
    };
    inp.click();
  }
  // 帖子列表项（rank 为排名序号，可选）
  function postItem(p, rank){
    var cover = p.cover ? '<img class="cover" loading="lazy" alt="" src="/api/community/img/' + p.cover + '?t=1">' : '';
    return '<a class="post" href="/community/post/' + p.id + '">' + (rank ? '<span class="rank' + (rank <= 3 ? ' t' : '') + '">' + rank + '</span>' : '') +
      avatar(p.author_id, p.author, p.has_avatar, 'sm') +
      '<div class="txt"><h3>' + esc(p.title) + '</h3><p class="ex">' + esc(p.excerpt) + '</p><div class="meta">' +
      (p.merchant ? '<span class="tag biz">商家</span>' : '') + '<span class="tag">' + esc(p.board_name) + '</span>' +
      '<span>' + esc(p.author) + badge(p.author_tier) + vb(p.author_verified) + '</span><span>' + esc(p.time) + '</span>' +
      '<span>❤️ ' + p.likes + '</span><span>💬 ' + p.comments + '</span>' + (p.img_n > 1 ? '<span>🖼 ' + p.img_n + '</span>' : '') +
      '</div></div>' + cover + '</a>';
  }
  // 交友卡片：locked = 未开通会员，只显示模糊照片
  function friendCard(c){
    var sub = [c.age ? c.age + '岁' : '', c.height ? c.height + 'cm' : '', c.city || ''].filter(Boolean).join(' · ');
    var g = '<span class="' + (c.gender === 'f' ? 'g-f">♀' : 'g-m">♂') + '</span> ';
    if (c.locked){
      var bg = c.blur ? ' style="background-image:url(/api/friends/photo/' + c.blur + '?blur=1)"' : '';
      return '<div class="fcard lk" data-lock="1"><div class="ph"' + bg + '></div><span class="lock">🔒</span>' +
        (c.purpose_name ? '<span class="pp">' + esc(c.purpose_name) + '</span>' : '') +
        '<div class="in"><div class="nm">' + g + esc(c.nickname) + '</div><div class="mt">' + esc(sub) + '</div></div></div>';
    }
    var bg2 = c.cover ? ' style="background-image:url(/api/friends/photo/' + c.cover + '?t=1)"' : '';
    return '<a class="fcard" href="/community/friends/' + c.uid + '"><div class="ph"' + bg2 + '>' + (c.cover ? '' : (c.gender === 'f' ? '👩' : '👨')) + '</div>' +
      (c.purpose_name ? '<span class="pp">' + esc(c.purpose_name) + '</span>' : '') +
      '<div class="in"><div class="nm">' + avatar(c.uid, c.nickname, c.has_avatar, 'sm') + g + esc(c.nickname) + vb(c.verified) + '</div><div class="mt">' + esc(sub) + '</div></div></a>';
  }
  function lockedPrompt(){
    me().then(function(d){
      if (!d.user){ if (confirm('登录并开通会员（¥' + d.prices.price_t1 + '）后可查看交友资料。现在登录 / 注册？')) location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search); }
      else if (confirm('开通会员（¥' + d.prices.price_t1 + '）后可查看清晰照片和完整资料。去了解会员？')) location.href = '/member';
    }).catch(function(){});
  }
  var ANTISCAM = '🛡️ 防骗提示：交友不谈钱。任何以投资、理财、虚拟币、借钱、垫付、“微信转账”等为由要钱的，一律是骗子；不要提供验证码、证件和银行卡；第一次见面请选人多的公共场所，并告诉朋友行程。遇到可疑账号请点“举报”。';
  return {prices: prices, esc: esc, api: api, me: me, badge: badge, showErr: showErr, qs: qs, compress: compress, userbar: userbar, report: report,
          vb: vb, avatar: avatar, pickAvatar: pickAvatar, postItem: postItem, friendCard: friendCard, lockedPrompt: lockedPrompt, ANTISCAM: ANTISCAM};
})();
