/* 开屏：后台开屏广告优先；未开启时展示三张品牌开屏（每次一张，按 1→2→3 轮换）。
   两种开屏均为 3 秒倒计时、可跳过，图片 1.5 秒内没加载好就放弃，绝不挡页面。 */
(function(){
  try {
    if (location.pathname !== '/' || new URLSearchParams(location.search).get('tab')) return;
    if (navigator.webdriver && !/splash=1/.test(location.search)) return;
  } catch(e){ return; }

  function safe(fn){
    return function(){
      try { return fn.apply(this, arguments); } catch(e){}
    };
  }

  function ev(e){
    try {
      fetch('/api/splash/ev', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({e: e}),
        keepalive: true
      });
    } catch(x){}
  }

  /* ---------- 1. 后台开屏广告（d.on） ---------- */
  var showAd = safe(function(d){
    var key = 'lh_splash_' + d.v;
    var today = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
    try {
      if (d.freq === 'daily' && localStorage.getItem(key) === today) return;
      if (d.freq === 'session' && sessionStorage.getItem(key)) return;
    } catch(e){}
    if (!d.img) return;

    var img = new Image();
    var done = false;
    var timer = setTimeout(safe(function(){
      if (done) return;
      done = true;
      img.onload = null;
      img.onerror = null;
    }), 1500);

    img.onload = safe(function(){
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { localStorage.setItem(key, today); sessionStorage.setItem(key, '1'); } catch(e){}

      var el = document.createElement('div');
      el.id = 'splash';
      el.innerHTML = '<div class="sp-ad" role="link" tabindex="0"></div>' +
        (d.label ? '<span class="sp-lbl">广告</span>' : '') +
        '<button type="button" class="sp-skip"><b>3</b>s 跳过</button>' +
        '<div class="sp-strip"><img src="/static/icons/icon-192.png?v=94" alt=""><b>旅韩助手<span>地铁 · 翻译 · 吃喝玩买</span></b></div>';
      el.querySelector('.sp-ad').style.backgroundImage = 'url("' + d.img + '")';
      document.body.appendChild(el);
      document.documentElement.classList.add('sp-on');
      ev('show');

      var n = 3, sk = el.querySelector('.sp-skip b');
      var t = setInterval(safe(function(){
        n--;
        if (n <= 0) close();
        else sk.textContent = n;
      }), 1000);

      function close(){
        clearInterval(t);
        el.classList.add('out');
        document.documentElement.classList.remove('sp-on');
        setTimeout(function(){ if (el.parentNode) el.parentNode.removeChild(el); }, 300);
      }

      el.querySelector('.sp-skip').onclick = safe(function(e){
        e.stopPropagation();
        ev('skip');
        close();
      });
      if (d.link){
        el.querySelector('.sp-ad').onclick = safe(function(){
          ev('click');
          close();
          location.href = d.link;
        });
      }
    });

    img.onerror = safe(function(){
      if (done) return;
      done = true;
      clearTimeout(timer);
    });
    img.src = d.img;
  });

  /* ---------- 2. 品牌开屏（后台未开启或接口失败） ---------- */
  var BRAND = [
    {webp: '/static/splash/splash-1.webp?v=94', jpg: '/static/splash/splash-1.jpg?v=94', bg: '#144254'},
    {webp: '/static/splash/splash-2.webp?v=94', jpg: '/static/splash/splash-2.jpg?v=94', bg: '#1E4A5E'},
    {webp: '/static/splash/splash-3.webp?v=94', jpg: '/static/splash/splash-3.jpg?v=94', bg: '#236C6A'}
  ];

  var showBrand = safe(function(){
    var i = 0;
    try {
      i = parseInt(localStorage.getItem('lh_brand_splash_i'), 10);
      if (!isFinite(i) || i < 0) i = 0;
    } catch(e){ i = 0; }
    var cur = i % 3;
    // 本次一开始就推进索引：即使这张加载失败，下次也会换下一张。
    try { localStorage.setItem('lh_brand_splash_i', String((cur + 1) % 3)); } catch(e){}

    var item = BRAND[cur];
    var img = new Image();
    var done = false;
    var timer = setTimeout(safe(function(){
      if (done) return;
      done = true;
      img.onload = null;
      img.onerror = null;
    }), 1500);

    img.onload = safe(function(){
      if (done) return;
      done = true;
      clearTimeout(timer);

      var el = document.createElement('div');
      el.id = 'splash';
      el.className = 'sp-brand';
      el.style.background = item.bg;
      el.innerHTML = '<div class="sp-ad" role="button" tabindex="0"></div>' +
        '<button type="button" class="sp-skip"><b>3</b>s 跳过</button>';
      el.querySelector('.sp-ad').style.backgroundImage = 'url("' + img.src + '")';
      document.body.appendChild(el);
      document.documentElement.classList.add('sp-on');

      var n = 3, sk = el.querySelector('.sp-skip b');
      var t = setInterval(safe(function(){
        n--;
        if (n <= 0) close();
        else sk.textContent = n;
      }), 1000);

      function close(){
        clearInterval(t);
        el.classList.add('out');
        document.documentElement.classList.remove('sp-on');
        setTimeout(function(){ if (el.parentNode) el.parentNode.removeChild(el); }, 300);
      }

      // 三张品牌图点画面都只关闭开屏（第 3 张画面里的「立即体验」不跳转）。
      el.querySelector('.sp-skip').onclick = safe(function(e){
        e.stopPropagation();
        close();
      });
      el.querySelector('.sp-ad').onclick = safe(function(){
        close();
      });
    });

    img.onerror = safe(function(){
      if (done) return;
      if (img.src.indexOf('.webp') !== -1){
        img.src = item.jpg; // webp 不可用时改用同名 jpg
        return;
      }
      done = true;
      clearTimeout(timer);
      img.onload = null;
      img.onerror = null;
    });

    img.src = item.webp;
  });

  try {
    fetch('/api/splash').then(function(r){ return r.json(); }).then(safe(function(d){
      if (d && d.on) showAd(d);
      else showBrand();
    })).catch(function(){
      showBrand();
    });
  } catch(e){}
})();
