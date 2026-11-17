/* 开屏广告：后台上传图片/跳转/频率/有效期；5 秒倒计时可跳过；左上角「广告」标识 */
(function(){
  try {
    if (new URLSearchParams(location.search).get('tab') || location.pathname !== '/') return;
    if (navigator.webdriver && !/splash=1/.test(location.search)) return;
  } catch(e){ return; }
  function ev(e){ try { fetch('/api/splash/ev', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({e: e}), keepalive: true}); } catch(x){} }
  fetch('/api/splash').then(function(r){ return r.json(); }).then(function(d){
    if (!d || !d.on) return;
    var key = 'lh_splash_' + d.v, today = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
    try {
      if (d.freq === 'daily' && localStorage.getItem(key) === today) return;
      if (d.freq === 'session' && sessionStorage.getItem(key)) return;
    } catch(e){}
    var img = new Image();
    var timer = setTimeout(function(){ img.onload = null; }, 2500); // 图片 2.5 秒内没加载好就不显示，不耽误用户
    img.onload = function(){
      clearTimeout(timer);
      try { localStorage.setItem(key, today); sessionStorage.setItem(key, '1'); } catch(e){}
      var el = document.createElement('div'); el.id = 'splash';
      el.innerHTML = '<div class="sp-ad" role="link" tabindex="0"></div>' + (d.label ? '<span class="sp-lbl">广告</span>' : '') +
        '<button type="button" class="sp-skip"><b>5</b>s 跳过</button>' +
        '<div class="sp-strip"><img src="/static/icons/icon-192.png" alt=""><b>旅韩助手<span>地铁 · 翻译 · 吃喝玩买</span></b></div>';
      el.querySelector('.sp-ad').style.backgroundImage = 'url("' + d.img + '")';
      document.body.appendChild(el); document.documentElement.classList.add('sp-on');
      ev('show');
      var n = 5, sk = el.querySelector('.sp-skip b');
      var t = setInterval(function(){ n--; if (n <= 0) close(); else sk.textContent = n; }, 1000);
      function close(){ clearInterval(t); el.classList.add('out'); document.documentElement.classList.remove('sp-on'); setTimeout(function(){ el.remove(); }, 300); }
      el.querySelector('.sp-skip').onclick = function(e){ e.stopPropagation(); ev('skip'); close(); };
      if (d.link){ el.querySelector('.sp-ad').onclick = function(){ ev('click'); close(); location.href = d.link; }; }
    };
    img.src = d.img;
  }).catch(function(){});
})();
