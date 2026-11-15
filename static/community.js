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
          '<span><a href="/community/u/' + d.user.id + '" style="color:inherit;text-decoration:none">' + esc(d.user.nickname) + '</a>' + badge(d.user.display_tier) + ubadges(d.user.badges, d.user.official, 3) + '</span></span>' +
          '<span class="row"><button class="btn sm ghost" type="button" id="lhAv">设置头像</button>' +
          '<a class="btn sm ghost" href="/points">🫘 ' + (d.user.points_balance != null ? d.user.points_balance : '') + ' 旅行豆</a>' +
          '<a class="btn sm ghost" href="/community/notices" style="position:relative">🔔 通知' + (d.user.notif_unread ? '<span class="dot">' + (d.user.notif_unread > 99 ? '99+' : d.user.notif_unread) + '</span>' : '') + '</a>' +
          '<a class="btn sm ghost" href="/consult" style="position:relative">💬 咨询<span class="dot" data-consult-dot' + (d.user.consult_unread ? '' : ' style="display:none"') + '>' + (d.user.consult_unread || '') + '</span></a>' +
          '<a class="btn sm ghost" href="/community/messages" style="position:relative">✉️ 私信' + (d.user.unread ? '<span class="dot">' + (d.user.unread > 99 ? '99+' : d.user.unread) + '</span>' : '') + '</a>' + (d.user.tier >= 2 ? '' : '<a class="btn sm primary" href="/member">' + (d.user.tier === 1 ? '升级高级会员' : '开通会员') + '</a>') +
          '<button class="btn sm gray" id="lhLogout">退出</button></span>';
        startConsultPoll();
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
  // 徽章（与 retention.py BADGES 同步）
  var BADGES = {first_post: ['✍️', '首帖'], posts10: ['📝', '笔耕不辍'], featured_author: ['🌟', '精选作者'], streak7: ['🔥', '连续签到7天'],
    streak30: ['🏆', '连续签到30天'], helper: ['💬', '热心回答'], daily_star: ['🇰🇷', '在韩达人'], official: ['✅', '官方认证'], good_merchant: ['🏅', '优质商家']};
  function ubadges(list, official, max){
    var h = official ? '<span class="offbadge">官方</span>' : '';
    (list || []).slice(-(max || 3)).forEach(function(k){ var b = BADGES[k]; if (b) h += '<span class="ubadge" title="' + b[1] + '">' + b[0] + '</span>'; });
    return h;
  }
  function postFlags(p){ return (p.pinned ? '<span class="tag pin">置顶</span>' : '') + (p.featured ? '<span class="tag feat">精选</span>' : '') + (p.topic ? '<span class="tag topic">话题</span>' : ''); }
  function postItem(p, rank){
    var cover = p.cover ? '<img class="cover" loading="lazy" alt="" src="/api/community/img/' + p.cover + '?t=1">' : '';
    return '<a class="post" href="/community/post/' + p.id + '">' + (rank ? '<span class="rank' + (rank <= 3 ? ' t' : '') + '">' + rank + '</span>' : '') +
      avatar(p.author_id, p.author, p.has_avatar, 'sm') +
      '<div class="txt"><h3>' + esc(p.title) + '</h3><p class="ex">' + esc(p.excerpt) + '</p><div class="meta">' +
      postFlags(p) + (p.merchant ? '<span class="tag biz">商家</span>' : '') + '<span class="tag">' + esc(p.board_name) + '</span>' +
      '<span>' + esc(p.author) + badge(p.author_tier) + vb(p.author_verified) + ubadges(p.author_badges, p.author_official, 2) + '</span><span>' + esc(p.time) + '</span>' +
      '<span>❤️ ' + p.likes + '</span><span>💬 ' + p.comments + '</span>' + (p.img_n > 1 ? '<span>🖼 ' + p.img_n + '</span>' : '') +
      '</div></div>' + cover + '</a>';
  }
  // 商家活动列表：标题 + 缩略图 + 阅读数 + 时间
  function bizItem(p){
    var cover = p.cover ? '<img class="cover" loading="lazy" alt="" src="/api/community/img/' + p.cover + '?t=1">' : '<span class="cover bzph">🏷️</span>';
    return '<a class="post biz-item" href="/community/post/' + p.id + '"><div class="txt"><h3>' + postFlags(p) + (p.tag_name ? '<span class="btag">[' + esc(p.tag_name) + ']</span>' : '') + esc(p.title) + '</h3>' +
      (p.offer ? '<p class="offer">🎁 ' + esc(p.offer) + '</p>' : '') +
      '<div class="meta"><span>👁 ' + (p.views || 0) + '</span><span>' + esc(p.time) + '</span>' + (p.valid_until ? '<span>有效至 ' + esc(p.valid_until) + '</span>' : '') + '</div></div>' + cover + '</a>';
  }
  function consultHref(kind, id){ return '/consult/new?kind=' + encodeURIComponent(kind) + '&id=' + encodeURIComponent(id); }
  function consultBtn(kind, id, label){ return '<a class="btn primary block consult-btn" href="' + consultHref(kind, id) + '">💬 ' + esc(label || '立即咨询') + '</a>'; }
  // 站内咨询未读提醒：页面角标 + （已授权时）系统通知
  var _cn = -1;
  async function consultPoll(){
    try {
      var r = await api('GET', '/api/consult/unread'), n = r.n || 0;
      document.querySelectorAll('[data-consult-dot]').forEach(function(el){ el.textContent = n > 99 ? '99+' : n; el.style.display = n ? '' : 'none'; });
      if (_cn >= 0 && n > _cn && document.hidden && window.Notification && Notification.permission === 'granted' && !/^\/consult\/\d/.test(location.pathname)){
        var opt = {body: '你有 ' + n + ' 条未读咨询消息', icon: '/static/icons/icon-192.png', tag: 'lh-consult', data: {url: '/consult'}};
        try { var reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration(); if (reg) reg.showNotification('旅韩助手 · 站内咨询', opt); else new Notification('旅韩助手 · 站内咨询', opt); } catch(e){}
      }
      _cn = n;
    } catch(e){}
  }
  function startConsultPoll(){ if (window._lhCP) return; window._lhCP = 1; consultPoll(); setInterval(function(){ if (!document.hidden || (window.Notification && Notification.permission === 'granted')) consultPoll(); }, 30000); }
  // 交友卡片：locked = 未开通会员，只显示模糊照片
  function friendCard(c){
    var sub = [c.age ? c.age + '岁' : '', c.height ? c.height + 'cm' : '', c.city || ''].filter(Boolean).join(' · ');
    var g = '<span class="' + (c.gender === 'f' ? 'g-f">♀' : 'g-m">♂') + '</span> ';
    var real = c.id_verified ? '<span class="vbadge">已实名</span>' : '';
    if (c.teaser || c.need_verify){
      return '<a class="fcard lk" href="/community/friends/verify"><div class="ph"></div><span class="lock">🪪</span>' +
        (c.purpose_name ? '<span class="pp">' + esc(c.purpose_name) + '</span>' : '') +
        '<div class="in"><div class="nm">' + g + esc(c.nickname) + '</div><div class="mt">' + esc(sub) + ' · 完成实名核验后可查看详情</div></div></a>';
    }
    if (c.locked){
      var bg = c.blur ? ' style="background-image:url(/api/friends/photo/' + c.blur + '?blur=1)"' : '';
      return '<div class="fcard lk" data-lock="1"><div class="ph"' + bg + '></div><span class="lock">🔒</span>' +
        (c.purpose_name ? '<span class="pp">' + esc(c.purpose_name) + '</span>' : '') +
        '<div class="in"><div class="nm">' + g + esc(c.nickname) + '</div><div class="mt">' + esc(sub) + '</div></div></div>';
    }
    var bg2 = c.cover ? ' style="background-image:url(/api/friends/photo/' + c.cover + '?t=1)"' : '';
    return '<a class="fcard" href="/community/friends/' + c.uid + '"><div class="ph"' + bg2 + '>' + (c.cover ? '' : (c.gender === 'f' ? '👩' : '👨')) + '</div>' +
      (c.purpose_name ? '<span class="pp">' + esc(c.purpose_name) + '</span>' : '') +
      '<div class="in"><div class="nm">' + avatar(c.uid, c.nickname, c.has_avatar, 'sm') + g + esc(c.nickname) + vb(c.verified) + real + '</div><div class="mt">' + esc(sub) + '</div></div></a>';
  }
  function lockedPrompt(){
    me().then(function(d){
      if (!d.user){ if (confirm('登录并开通会员（¥' + d.prices.price_t1 + '）后可查看交友资料。现在登录 / 注册？')) location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search); }
      else if (confirm('开通会员（¥' + d.prices.price_t1 + '）后可查看清晰照片和完整资料。去了解会员？')) location.href = '/member';
    }).catch(function(){});
  }
  var ANTISCAM = '🛡️ 防骗提示：交友不谈钱。任何以投资、理财、虚拟币、借钱、垫付、“微信转账”等为由要钱的，一律是骗子；不要提供验证码、证件和银行卡；第一次见面请选人多的公共场所，并告诉朋友行程。遇到可疑账号请点“举报”。';
  async function share(opts){
    opts = opts || {};
    var url = opts.url || (location.origin + location.pathname + location.search);
    var title = opts.title || document.title || '旅韩助手';
    var text = opts.text || '';
    var usedShare = false;
    try {
      if (navigator.share){
        await navigator.share({title: title, text: text, url: url});
        usedShare = true;
      }
    } catch(e){
      if (e && e.name === 'AbortError') return {ok: false, aborted: true};
    }
    if (!usedShare){
      try {
        if (navigator.clipboard && navigator.clipboard.writeText){ await navigator.clipboard.writeText(url); alert('链接已复制，可以发给朋友'); }
        else { prompt('复制链接发给朋友', url); }
      } catch(e){ prompt('复制链接发给朋友', url); }
    }
    if (opts.targetType && opts.targetId){
      try {
        var r = await api('POST', '/api/points/event', {kind: 'share', target_type: opts.targetType, target_id: String(opts.targetId)});
        if (r && r.awarded) alert('分享成功，旅行豆 +' + r.awarded + '（余额 ' + r.balance + '）');
        else if (r && r.reason) { /* 已达上限或已领过：不打断分享 */ }
      } catch(e){
        if (e.status !== 401) { /* 未登录不提示奖励 */ }
      }
    }
    return {ok: true};
  }
  function mapLinks(address){
    var q = encodeURIComponent(address || '');
    return {
      naver: 'https://map.naver.com/p/search/' + q,
      kakao: 'https://map.kakao.com/?q=' + q,
      google: 'https://www.google.com/maps/search/?api=1&query=' + q
    };
  }
  function copyText(text, okMsg){
    text = String(text || '');
    function done(){ if (okMsg) alert(okMsg); }
    if (navigator.clipboard && navigator.clipboard.writeText){
      return navigator.clipboard.writeText(text).then(done).catch(function(){ prompt('复制下面的内容', text); });
    }
    prompt('复制下面的内容', text);
  }
  function krwCny(krw, rate){
    if (!rate) return '';
    return '（约 ¥' + (Number(krw) / Number(rate)).toFixed(2) + '）';
  }
  function menuHtml(menu, rate){
    menu = menu || [];
    if (!menu.length) return '';
    return '<ul class="shop-menu" style="margin:8px 0;padding-left:18px;font-size:14px;line-height:1.7">' +
      menu.map(function(m){
        var cny = m.price_cny != null ? ('（约 ¥' + m.price_cny + '）') : krwCny(m.price_krw, rate);
        return '<li>' + esc(m.name) + ' · ' + m.price_krw + ' 韩元' + cny + '</li>';
      }).join('') + '</ul>';
  }
  function locHtml(shop, opts){
    opts = opts || {};
    var addr = shop.address || '';
    if (!addr) return '';
    var links = shop.map_links || mapLinks(addr);
    var sid = shop.id, typ = opts.targetType || 'shop';
    return '<div class="shop-loc" style="margin:8px 0 4px"><div class="tip" style="margin:0 0 6px">地址：' + esc(addr) + '</div>' +
      '<button class="btn sm ghost" type="button" data-copy-addr="' + esc(addr) + '">复制地址</button>' +
      '<a class="btn sm ghost" href="' + esc(links.naver) + '" target="_blank" rel="noopener noreferrer">Naver 地图</a>' +
      '<a class="btn sm ghost" href="' + esc(links.kakao) + '" target="_blank" rel="noopener noreferrer">Kakao 地图</a>' +
      '<a class="btn sm ghost" href="' + esc(links.google) + '" target="_blank" rel="noopener noreferrer">Google 地图</a>' +
      (opts.share === false ? '' : '<button class="btn sm ghost" type="button" data-share="' + sid + '" data-kind="' + typ + '" data-name="' + esc(shop.name || '') + '">分享</button>') +
      '</div>';
  }
  function starsHtml(v){
    v = Math.max(0, Math.min(5, Math.round(Number(v) || 0)));
    return '<span class="stars" aria-label="' + v + ' 星">' + '★★★★★'.slice(0, v) + '<span class="off">' + '★★★★★'.slice(v) + '</span></span>';
  }
  function priceLine(shop, rate){
    if (!shop.price_text) return '';
    var t = '人均 ' + shop.price_text;
    if (rate && (shop.price_min != null || shop.price_max != null)){
      var a = shop.price_min != null ? shop.price_min : shop.price_max, b = shop.price_max != null ? shop.price_max : shop.price_min;
      var ca = Math.round(a / Number(rate)), cb = Math.round(b / Number(rate));
      t += '（约 ¥' + (ca === cb ? ca : (ca + '–' + cb)) + '）';
    }
    return t;
  }
  function ratingText(shop){
    if (!shop.rating_count) return '<span class="tip" style="margin:0">暂无评价</span>';
    return '<span>★ ' + shop.rating_avg + '（' + shop.rating_count + ' 条评价）</span>';
  }
  function bindShopExtras(root, opts){
    opts = opts || {};
    root.querySelectorAll('[data-copy-addr]').forEach(function(b){
      b.onclick = function(){ copyText(b.getAttribute('data-copy-addr'), '地址已复制'); };
    });
    if (opts.onShare){
      root.querySelectorAll('[data-share]').forEach(function(b){
        b.onclick = function(){ opts.onShare(b); };
      });
    }
  }
  return {prices: prices, esc: esc, api: api, me: me, badge: badge, showErr: showErr, qs: qs, compress: compress, userbar: userbar, report: report,
          vb: vb, avatar: avatar, pickAvatar: pickAvatar, postItem: postItem, bizItem: bizItem, ubadges: ubadges, BADGES: BADGES, postFlags: postFlags, consultHref: consultHref, consultBtn: consultBtn, startConsultPoll: startConsultPoll, friendCard: friendCard, lockedPrompt: lockedPrompt, ANTISCAM: ANTISCAM, share: share,
          mapLinks: mapLinks, copyText: copyText, krwCny: krwCny, menuHtml: menuHtml, locHtml: locHtml, ratingText: ratingText, bindShopExtras: bindShopExtras, starsHtml: starsHtml, priceLine: priceLine};
})();
