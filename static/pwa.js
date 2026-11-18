/* 旅韩助手：全站底部导航 + 注册 Service Worker + 可关闭的安装提示 */
(function(){

  /* 开业活动脚本：红标「我的」+ 首页每日弹层 */
  if (!document.querySelector('script[data-lh-opening]')) {
    var s = document.createElement('script');
    s.src = '/static/opening-promos.js?v=77';
    s.async = true;
    s.setAttribute('data-lh-opening', '1');
    document.head.appendChild(s);
  }

  /* ===================== 底部七格导航（必须在安装提示的 return 之前执行） ===================== */
  var TABS = [
    {k:'home',     href:'/',                    icon:'🏠', label:'首页'},
    {k:'follow',   href:'/community?tab=follow', icon:'👥', label:'关注'},
    {k:'post',     href:'/community/new',        icon:'＋', label:'发布', pub:true},
    {k:'festival', href:'/festival',             icon:'🎁', label:'购物节', fest:true},
    {k:'services', href:'/shops',                icon:'💼', label:'商业服务', cls:'svc'},
    {k:'trips',    href:'/trips',                icon:'🗓️', label:'行程安排', cls:'loc'},
    {k:'me',       href:'/me',                   icon:'👤', label:'我的', cls:'me'}
  ];

  function currentKey(){
    var p = location.pathname, q = new URLSearchParams(location.search || '');
    if (p === '/me' || p === '/static/me.html') return 'me';
    if (p === '/festival' || p === '/static/festival.html') return 'festival';
    if (p === '/trips' || p === '/static/trips.html' || p === '/local' || p === '/static/local.html') return 'trips';
    if (p === '/community/new') return 'post';
    if (p === '/services' || p === '/static/services.html') return 'services';
    if (p === '/shops' || p === '/static/shops.html') return 'services';
    if (p === '/community' || p === '/static/community.html') {
      return q.get('tab') === 'follow' ? 'follow' : '';
    }
    if (p === '/' || p === '/index.html' || p === '/static/index.html') {
      var tab = q.get('tab');
      return (!tab || tab === 'home') ? 'home' : '';
    }
    return '';
  }

  function tabbarHTML(){
    return TABS.map(function(t){
      if (t.pub) {
        return '<a href="' + t.href + '" data-nav="' + t.k + '" class="pub"><span class="plus">＋</span>' + t.label + '</a>';
      }
      if (t.fest) {
        return '<a href="' + t.href + '" data-nav="' + t.k + '" class="fest"><span class="fest-ico">🎁<em>冬季</em></span>' + t.label + '</a>';
      }
      return '<a href="' + t.href + '" data-nav="' + t.k + '"' + (t.cls ? ' class="' + t.cls + '"' : '') +
        '><i>' + t.icon + '</i>' + t.label + '</a>';
    }).join('');
  }

  function buildTabbar(){
    var nav = document.createElement('nav');
    nav.className = 'tabbar';
    nav.setAttribute('aria-label', '底部导航');
    nav.innerHTML = tabbarHTML();
    return nav;
  }

  function looksLikeOldNav(el){
    if (!el || el.tagName !== 'NAV') return false;
    var txt = el.textContent || '', html = el.innerHTML || '', n = el.querySelectorAll('a,button').length;
    return n >= 4 && n <= 8 && (
      /地铁|全部|我的|韩友圈|首页|翻译/.test(txt) ||
      /tab=sub|tab=home|tab=card|tab=more|data-tab/.test(html)
    );
  }

  function onHomeDoc(){
    var p = location.pathname;
    return p === '/' || p === '/index.html' || p === '/static/index.html';
  }

  window.lhGoBack = function(fallback){
    fallback = fallback || '/';
    var same = false;
    try {
      same = !!(document.referrer && (new URL(document.referrer)).origin === location.origin);
    } catch (e) {}
    if (same && history.length > 1) history.back();
    else location.href = fallback;
  };

  function bindHomeLink(nav){
    if (nav.dataset.homeBound === '1') return; // 页面已自行绑定（index.html 内联脚本）时不重复绑定
    nav.dataset.homeBound = '1';
    nav.addEventListener('click', function(e){
      var a = e.target.closest ? e.target.closest('a[data-nav]') : null;
      if (!a) return;
      // 立即点亮，跳转仍走 a.href，不 intercept 非首页文档
      var links = nav.querySelectorAll('a[data-nav]');
      for (var i = 0; i < links.length; i++) links[i].classList.toggle('on', links[i] === a);
      var key = a.getAttribute('data-nav');
      if (key === 'home' && onHomeDoc() && typeof window.showTab === 'function') {
        e.preventDefault();
        document.body.classList.remove('lh-overlay-open');
        window.showTab('home', true);
      }
    });
  }

  function isBottomNav(el){
    if (!el || el.tagName !== 'NAV') return false;
    if (el.closest && el.closest('header,aside,.sheet,.bar,.topbar')) return false;
    var n = el.querySelectorAll('a,button').length;
    return n >= 3 && n <= 8;
  }

  function normalizeTabbar(){
    if (!document.body) return;
    var nav = document.querySelector('nav.tabbar');
    if (!nav) {
      var old = document.querySelector('nav');
      var fresh = buildTabbar();
      if (old && (looksLikeOldNav(old) || isBottomNav(old))) {
        // 旧版底部 nav（地铁 / 翻译 / 韩友圈 / 全部，或上一版五格）统一换成七格
        old.parentNode.replaceChild(fresh, old);
      } else {
        // 页面本来没有底部导航：补一个
        document.body.appendChild(fresh);
      }
      nav = fresh;
    }
    var expect = TABS.map(function(t){ return t.k; }).join(',');
    var have = Array.prototype.map.call(nav.querySelectorAll('a[data-nav]'), function(a){ return a.getAttribute('data-nav'); }).join(',');
    if (have !== expect) nav.innerHTML = tabbarHTML();
    var key = currentKey();
    var links = nav.querySelectorAll('a[data-nav]');
    for (var i = 0; i < links.length; i++) {
      var on = !!key && links[i].getAttribute('data-nav') === key;
      links[i].classList.toggle('on', on);
      if (on) links[i].setAttribute('aria-current', 'page');
      else links[i].removeAttribute('aria-current');
    }
    document.body.classList.add('has-tabbar');
    bindHomeLink(nav);
  }

  function refreshNav(){
    var sh = document.getElementById('openPromoSheet');
    if (!sh || sh.hidden) document.body.classList.remove('lh-overlay-open');
    normalizeTabbar();
    if (window.LHOpening && LHOpening.paintMeTab) {
      LHOpening.load().then(function(cfg){ if (cfg) LHOpening.paintMeTab(cfg); });
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refreshNav);
  } else {
    refreshNav();
  }
  window.addEventListener('pageshow', refreshNav);

  function paintMeBadge(n){
    n = +n || 0;
    var a = document.querySelector('nav.tabbar a[data-nav="me"]');
    if (!a) return;
    var d = a.querySelector('.tab-dot');
    if (!n){
      if (d) d.hidden = true;
      return;
    }
    if (!d){
      d = document.createElement('span');
      d.className = 'tab-dot';
      a.appendChild(d);
    }
    d.hidden = false;
    d.textContent = n > 99 ? '99+' : String(n);
  }
  function loadMeBadge(){
    try {
      fetch('/api/me/summary', {credentials: 'same-origin', headers: {'X-LH': '1'}}).then(function(r){
        return r.json();
      }).then(function(j){
        var u = j && j.user;
        if (!u){ paintMeBadge(0); return; }
        paintMeBadge((u.notif_unread || 0) + (u.consult_unread || 0) + (u.msg_unread || 0));
      }).catch(function(){});
    } catch (e) {}
  }
  window.addEventListener('pageshow', loadMeBadge);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadMeBadge);
  } else {
    loadMeBadge();
  }
  /* =================== 底部六格导航结束 =================== */

  if('serviceWorker' in navigator){
    window.addEventListener('load', function(){
      navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(function(){});
    });
  }

  var KEY = 'lvhan-pwa-hint-v1';
  function standalone(){
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
      window.navigator.standalone === true;
  }
  function isIosSafari(){
    var ua = navigator.userAgent || '';
    var iOS = /iPhone|iPad|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    var isSafari = /WebKit/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS|Android/.test(ua);
    return iOS && isSafari;
  }
  if(standalone() || localStorage.getItem(KEY) === '1') return;

  var deferred = null;
  var bar = null;

  function hide(){
    if(bar){ bar.remove(); bar = null; }
    document.body.classList.remove('pwa-hasbar');
    try{ localStorage.setItem(KEY, '1'); }catch(e){}
  }

  function show(html, onInstall){
    if(bar || document.getElementById('pwaBar')) return;
    bar = document.createElement('div');
    bar.id = 'pwaBar';
    bar.setAttribute('role', 'dialog');
    bar.innerHTML = html;
    document.body.appendChild(bar);
    document.body.classList.add('pwa-hasbar');
    var x = bar.querySelector('.pwa-x');
    if(x) x.addEventListener('click', hide);
    var ins = bar.querySelector('.pwa-install');
    if(ins && onInstall) ins.addEventListener('click', onInstall);
  }

  window.addEventListener('beforeinstallprompt', function(e){
    e.preventDefault();
    deferred = e;
    show(
      '<div class="pwa-txt">把「旅韩助手」添加到主屏幕，离线也能看景点和常用句。</div>' +
      '<button type="button" class="pwa-install">添加到主屏幕</button>' +
      '<button type="button" class="pwa-x" aria-label="关闭">×</button>',
      function(){
        if(!deferred) return;
        deferred.prompt();
        deferred.userChoice.finally(function(){ deferred = null; hide(); });
      }
    );
  });

  window.addEventListener('appinstalled', hide);

  if(isIosSafari()){
    show(
      '<div class="pwa-txt">在 Safari 点底部分享按钮，再选「添加到主屏幕」，即可离线查看景点和常用句。</div>' +
      '<button type="button" class="pwa-x" aria-label="关闭">×</button>'
    );
  }
})();
