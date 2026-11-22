/* 旅韩助手 · 社区通用脚本 */
/* 头像加载失败时回退到首字母（替代内联 onerror，配合内容安全策略 CSP） */
document.addEventListener('error', function(ev){
  var t = ev.target;
  if (t && t.tagName === 'IMG' && t.getAttribute('data-avfb') === '1'){
    t.style.display = 'none';
    if (t.parentNode && t.parentNode.classList) t.parentNode.classList.add('fb');
  }
  if (t && t.tagName === 'IMG' && t.getAttribute('data-hideonerr') === '1') t.style.display = 'none';
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
  // 浏览器端压缩：最长边 1600px，JPEG 0.82。必须永远返回全新的内存 File，不能把
  // 相册 input / IndexedDB 里的原始句柄直接塞进 FormData（iOS WebKit 会发出空请求体）。
  function isGifFile(file){ return /gif/i.test((file && file.type) || '') || /\.gif$/i.test((file && file.name) || ''); }
  function isSimpleRaster(file){ return /image\/(jpeg|jpg|png|webp|gif|bmp)/i.test((file && file.type) || '') || /\.(jpe?g|png|webp|gif|bmp)$/i.test((file && file.name) || ''); }
  async function asMemoryFile(file, name, type){
    if (!file) throw new Error('图片读取失败，请重新选择');
    var buf = file.buf || null;
    if (!buf){
      try { buf = await file.arrayBuffer(); } catch(e){ throw new Error('有图片读取失败，可能是 iCloud 原图未下载，请重试'); }
    }
    if (!buf || buf.byteLength === 0) throw new Error('有图片读取失败，可能是 iCloud 原图未下载，请重试');
    return new File([buf], name || file.name || 'img.jpg', {type: type || file.type || 'image/jpeg'});
  }
  function compress(file){
    return (async function(){
      var mem = await asMemoryFile(file);
      if (isGifFile(mem)) return mem;
      var forceJpeg = !isSimpleRaster(mem) || /heic|heif|avif|tiff/i.test((mem.type || '') + ' ' + (mem.name || ''));
      return await new Promise(function(resolve, reject){
        var url = URL.createObjectURL(mem), img = new Image();
        img.onload = function(){
          var max = 1600, w = img.naturalWidth, h = img.naturalHeight, k = Math.min(1, max / Math.max(w, h) || 1);
          var c = document.createElement('canvas'); c.width = Math.max(1, Math.round(w * k)); c.height = Math.max(1, Math.round(h * k));
          c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          URL.revokeObjectURL(url);
          c.toBlob(function(b){
            if (b && b.size){
              if (forceJpeg || b.size < mem.size) return resolve(new File([b], 'img.jpg', {type: 'image/jpeg'}));
            } else if (forceJpeg){
              return reject(new Error('图片无法转换，请换 JPG 或 PNG 再试'));
            }
            asMemoryFile(mem, mem.name || 'img.jpg', mem.type).then(resolve, reject);
          }, 'image/jpeg', 0.82);
        };
        img.onerror = function(){
          URL.revokeObjectURL(url);
          if (forceJpeg) return reject(new Error('图片无法识别，请换 JPG 或 PNG 再试'));
          asMemoryFile(mem, mem.name || 'img.jpg', mem.type).then(resolve, reject);
        };
        img.src = url;
      });
    })();
  }
  async function userbar(el){
    // 顶部个人卡片已下线，相关入口统一由「我的」页提供。
    // 保留 el 参数兼容旧调用：el 可能为 null，也可能没有 innerHTML，这里先做空值保护；
    // 若旧页面仍把卡片容器传进来，只移除该容器，不再渲染任何个人卡片内容。
    if (el && el.innerHTML !== undefined && el.parentNode && el.classList && el.classList.contains('userbar')){
      el.parentNode.removeChild(el);
    }
    try {
      var d = await me();
      if (d && d.user) startConsultPoll();
      return d;
    } catch(e){
      return null;
    }
  }
  async function report(type, id){
    var d = await me().catch(function(){ return {}; });
    if (!d.user){ location.href = '/login?next=' + encodeURIComponent(location.pathname); return; }
    if (d.user.tier < 1 && type !== 'message' && type !== 'user'){ if (confirm('开通会员后才能举报。去了解会员？')) location.href = '/member'; return; }
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
    return ['#146E68','#5b7fd6','#2f9e7a','#d4a017','#8e6bb8','#e07a5f','#4a9aa3','#B85A12'][h % 8];
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
  function relTime(s){
    if (!s) return '';
    var m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (!m) return s;
    var d = new Date(m[1] + '-' + m[2] + '-' + m[3] + 'T' + m[4] + ':' + m[5] + ':00+09:00');
    if (isNaN(d.getTime())) return s;
    var sec = Math.round((Date.now() - d.getTime()) / 1000);
    if (sec < 45) return '刚刚';
    if (sec < 3600) return Math.max(1, Math.floor(sec / 60)) + '分钟前';
    if (sec < 86400) return Math.floor(sec / 3600) + '小时前';
    if (sec < 172800) return '昨天 ' + m[4] + ':' + m[5];
    if (d.getFullYear() === new Date().getFullYear()) return m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5];
    return s;
  }
  function toast(msg, kind, ms){
    var el = document.getElementById('lhToast');
    if (!el){
      el = document.createElement('div');
      el.id = 'lhToast';
      el.className = 'lh-toast';
      el.setAttribute('role', 'status');
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = 'lh-toast show' + (kind === 'ok' ? ' ok' : '');
    clearTimeout(el._t);
    el._t = setTimeout(function(){ el.classList.remove('show'); }, ms || (kind === 'ok' ? 2800 : 2500));
  }
  function postPhotos(p){
    var ids = (p.thumbs && p.thumbs.length) ? p.thumbs : (p.cover ? [p.cover] : []);
    if (!ids.length) return '';
    function img(id, cls){
      return '<img' + (cls ? ' class="' + cls + '"' : '') + ' alt="" decoding="async" src="/api/community/img/' + id + '?t=1" onerror="if(this.dataset.fb){this.onerror=null;return;}this.dataset.fb=1;this.src=\'/api/community/img/' + id + '\';">';
    }
    if (ids.length === 1) return img(ids[0], 'photo');
    var n = p.img_n || ids.length;
    var cells = ids.slice(0, 3).map(function(id, i){
      var more = (i === 2 && n > 3) ? '<span class="more">+' + (n - 2) + '</span>' : '';
      return '<span class="ph">' + more + img(id, '') + '</span>';
    }).join('');
    return '<div class="photos n' + Math.min(ids.length, 3) + '">' + cells + '</div>';
  }
  function postItem(p, rank){
    var pin = (p.pinned ? '置顶 · ' : '') + (p.featured ? '精选 · ' : '');
    var title = (p.tag_name ? '[' + p.tag_name + '] ' : '') + p.title;
    return '<a class="post" href="/community/post/' + p.id + '">' + (rank ? '<span class="rank' + (rank <= 3 ? ' t' : '') + '">' + rank + '</span>' : '') +
      '<div class="author">' + avatar(p.author_id, p.author, p.has_avatar, 'xs') +
      '<div class="who">' + esc(p.author) + '<small>' + esc(relTime(p.time)) + '</small></div></div>' +
      '<h3>' + (pin ? '<span style="color:var(--primary);font-weight:700;font-size:13px">' + pin + '</span>' : '') + esc(title) + '</h3>' +
      (p.excerpt ? '<p class="ex">' + esc(p.excerpt) + '</p>' : '') +
      (p.offer ? '<div class="offer">🎁 ' + esc(p.offer) + '</div>' : '') +
      postPhotos(p) +
      '<div class="meta"><span class="tag">' + esc(p.board_name) + '</span>' +
      '<span class="acts"><span>🤍 ' + p.likes + '</span><span>💬 ' + p.comments + '</span></span></div></a>';
  }
  function coverId(p){
    if (p.thumbs && p.thumbs.length) return p.thumbs[0];
    if (p.cover) return p.cover;
    return null;
  }
  function waterItem(p){
    var id = coverId(p);
    var title = esc(p.title || '');
    var likes = (p.likes == null ? 0 : p.likes);
    var cover;
    if (id){
      var src = '/api/community/img/' + id + '?t=1';
      var full = '/api/community/img/' + id;
      cover = '<div class="wcover" style="aspect-ratio:3/4"><img alt="" decoding="async" data-src="' + src + '" data-full="' + full + '"></div>';
    } else {
      cover = '<div class="wcover wtext" style="aspect-ratio:3/4"><div class="wtext-t">' + title + '</div></div>';
    }
    if (!id && !title){
      cover = '<div class="wcover" style="aspect-ratio:3/4"></div>';
    }
    var spot = esc(p.spot_name || '');
    var sarea = esc(p.spot_area || '');
    var spotLine = spot ? ('<div class="wspot">' + spot + (sarea ? '<span class="warea"> · ' + sarea + '</span>' : '') + '</div>') : '';
    var cmts = (p.comments == null ? 0 : p.comments);
    var uid = p.author_id;
    var off = p.author_official ? '<span class="woff">官方</span>' : '';
    var userInner = avatar(uid, p.author, p.has_avatar, 'xs') + '<span class="wnick">' + esc(p.author) + '</span>' + off;
    var user = uid ? ('<a class="wuser-a" href="/community/u/' + uid + '">' + userInner + '</a>') : ('<span class="wuser">' + userInner + '</span>');
    return '<article class="wcard">' +
      '<a class="wlink" href="/community/post/' + p.id + '">' + cover +
      (title ? '<div class="wtitle">' + title + '</div>' : '') +
      spotLine + '</a>' +
      '<div class="wmeta"><span class="wuser">' + user + '</span>' +
      '<span class="wstats"><span class="wlike">♡ ' + likes + '</span><span class="wcmt">💬 ' + cmts + '</span></span></div></article>';
  }
  var _wIO = null;
  function bindWaterLazy(root){
    if (typeof IntersectionObserver === 'undefined'){
      (root || document).querySelectorAll('.wcard img[data-src]').forEach(function(img){
        if (!img.getAttribute('src')){
          img.onerror = function(){ if (img.dataset.fb) return; img.dataset.fb = '1'; var f = img.getAttribute('data-full'); if (f) img.src = f; };
          img.src = img.getAttribute('data-src');
        }
      });
      return;
    }
    if (!_wIO){
      _wIO = new IntersectionObserver(function(entries){
        entries.forEach(function(en){
          if (!en.isIntersecting) return;
          var img = en.target;
          _wIO.unobserve(img);
          var src = img.getAttribute('data-src');
          if (!src || img.getAttribute('src')) return;
          img.onload = function(){
            var w = img.naturalWidth, h = img.naturalHeight;
            if (!w || !h) return;
            var r = w / h;
            if (r < 0.75) r = 0.75;
            if (r > 4/3) r = 4/3;
            var box = img.parentElement;
            if (box && box.classList.contains('wcover')) box.style.aspectRatio = String(r);
          };
          img.onerror = function(){
            if (img.dataset.fb) return;
            img.dataset.fb = '1';
            var full = img.getAttribute('data-full');
            if (full) img.src = full;
          };
          img.src = src;
        });
      }, {rootMargin: '800px 0px', threshold: 0.01});
    }
    (root || document).querySelectorAll('.wcard img[data-src]:not([src])').forEach(function(img){ _wIO.observe(img); });
  }
  function shorterCol(cols){
    if (!cols || !cols.length) return null;
    if (cols.length < 2) return cols[0];
    return cols[0].offsetHeight <= cols[1].offsetHeight ? cols[0] : cols[1];
  }
  function appendWater(listEl, posts, reset, leadHtml){
    if (reset){
      listEl.className = 'waterfall';
      listEl.innerHTML = (leadHtml || '') + '<div class="wcols"><div class="wcol"></div><div class="wcol"></div></div>';
    }
    var cols = listEl.querySelectorAll('.wcol');
    (posts || []).forEach(function(p){
      var col = shorterCol(cols);
      if (!col) return;
      col.insertAdjacentHTML('beforeend', waterItem(p));
    });
    bindWaterLazy(listEl);
  }
  function bizItem(p){ return postItem(p); }
  function consultHref(kind, id){ return '/consult/new?kind=' + encodeURIComponent(kind) + '&id=' + encodeURIComponent(id); }
  function consultBtn(kind, id, label){ return '<a class="btn primary block consult-btn" href="' + consultHref(kind, id) + '">💬 ' + esc(label || '立即咨询') + '</a>'; }
  // 站内咨询未读提醒：页面角标 + （已授权时）系统通知
  var _cn = -1;
  async function consultPoll(){
    try {
      var r = await api('GET', '/api/consult/unread'), n = r.n || 0;
      document.querySelectorAll('[data-consult-dot]').forEach(function(el){ el.textContent = n > 99 ? '99+' : n; el.style.display = n ? '' : 'none'; });
      if (_cn >= 0 && n > _cn && document.hidden && window.Notification && Notification.permission === 'granted' && !/^\/consult\/\d/.test(location.pathname)){
        var opt = {body: '你有 ' + n + ' 条未读咨询消息', icon: '/static/icons/icon-192.png?v=95', tag: 'lh-consult', data: {url: '/consult'}};
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
  return {prices: prices, esc: esc, api: api, me: me, badge: badge, showErr: showErr, qs: qs, compress: compress, asMemoryFile: asMemoryFile, userbar: userbar, report: report, toast: toast, relTime: relTime,
          vb: vb, avatar: avatar, pickAvatar: pickAvatar, postItem: postItem, waterItem: waterItem, appendWater: appendWater, bindWaterLazy: bindWaterLazy, bizItem: bizItem, ubadges: ubadges, BADGES: BADGES, postFlags: postFlags, consultHref: consultHref, consultBtn: consultBtn, startConsultPoll: startConsultPoll, friendCard: friendCard, lockedPrompt: lockedPrompt, ANTISCAM: ANTISCAM, share: share,
          mapLinks: mapLinks, copyText: copyText, krwCny: krwCny, menuHtml: menuHtml, locHtml: locHtml, ratingText: ratingText, bindShopExtras: bindShopExtras, starsHtml: starsHtml, priceLine: priceLine};
})();
