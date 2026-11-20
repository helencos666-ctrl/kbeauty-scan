/* 商家服务分类列表：示范案例 + 已审核商家。示范案例有明确标注，不是真实商家。 */
(function(){
  document.addEventListener('error', function(ev){
    var t = ev.target;
    if (t && t.tagName === 'IMG' && t.getAttribute('data-hideonerr') === '1') t.style.display = 'none';
  }, true);
  var CATS = {
    guide:"地陪向导", clinic:"医美", food:"美食代点外卖", airport:"接送机", hotel:"住宿",
    interp:"翻译", kpop:"追星活动", car:"租车", sim:"通讯电话卡", logistics:"物流快递",
    daigou:"代购", other:"其它"
  };
  var box = document.getElementById('catChips');
  var list = document.getElementById('svcList');
  var title = document.getElementById('feedTitle');
  if (!box || !list || !window.LH) return;

  function catFromUrl(){
    var c = (LH.qs('cat') || '').trim();
    if (CATS[c]) return c;
    return '';
  }
  function mark(cat){
    box.querySelectorAll('[data-cat]').forEach(function(x){
      var on = cat && x.getAttribute('data-cat') === cat;
      x.classList.toggle('on', on);
      x.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }
  function cardExample(ex){
    var cover = (ex.photos && ex.photos[0]) ? '<a class="mcover" href="' + LH.esc(ex.href) + '"><img loading="lazy" alt="示范照片（示意）" src="' + LH.esc(ex.photos[0].src) + '"></a>' : '';
    return '<article class="mpost example-card">' + cover + '<div class="mbody">' +
      '<span class="ex-badge">' + LH.esc(ex.badge) + '</span>' +
      '<h3><a href="' + LH.esc(ex.href) + '">' + LH.esc(ex.name) + '</a></h3>' +
      '<p class="ex">' + LH.esc(ex.intro) + '</p>' +
      '<div class="mfoot"><span class="mmeta">' + LH.esc(ex.area) + '</span>' +
      '<a class="btn ghost consult-btn" href="' + LH.esc(ex.href) + '">查看示范</a></div>' +
      '</div></article>';
  }
  function cardProvider(it){
    var href = '/shops/p/' + it.id;
    var cover = it.photo ? '<a class="mcover" href="' + href + '"><img loading="lazy" alt="" src="' + LH.esc(it.photo) + '"></a>' : '';
    var n = (it.photos && it.photos.length) ? it.photos.length : (it.photo ? 1 : 0);
    return '<article class="mpost">' + cover + '<div class="mbody">' +
      '<span class="tag">' + LH.esc(it.category_name || '') + '</span>' +
      '<h3><a href="' + href + '">' + LH.esc(it.name) + '</a></h3>' +
      (it.services ? '<p class="ex">' + LH.esc(it.services) + '</p>' : '') +
      '<div class="mfoot"><span class="mmeta">' + LH.esc(it.area || '') + (n ? ' · ' + n + ' 张照片' : '') + '</span>' +
      LH.consultBtn('provider', it.id, '站内咨询') + '</div>' +
      '</div></article>';
  }

  async function load(cat){
    mark(cat);
    if (!cat){
      list.innerHTML = '';
      list.hidden = true;
      if (title) title.textContent = '商家服务';
      return;
    }
    list.hidden = false;
    if (title) title.textContent = CATS[cat] || '商家服务';
    list.innerHTML = '<div class="loading">加载中…</div>';
    var ex = null, items = [], loadErr = null;
    try { ex = (await LH.api('GET', '/api/shop-examples/' + encodeURIComponent(cat))).item; } catch(e){ ex = null; }
    try { items = (await LH.api('GET', '/api/providers?cat=' + encodeURIComponent(cat))).items || []; } catch(e){
      loadErr = e;
      items = [];
    }
    var html = '';
    if (ex) html += cardExample(ex);
    items.forEach(function(it){ html += cardProvider(it); });
    if (loadErr){
      html += '<div class="load-err">商家列表暂时加载失败：' + LH.esc(loadErr.message || '请稍后再试') +
        '<br><button class="btn ghost" type="button" id="svcRetry">重试</button></div>';
    } else if (!items.length){
      html += '<div class="empty-state"><p>这一类还没有入驻商家。上面是填写格式示范，不是真实商家。</p>' +
        '<a class="btn primary" href="/shops/join-guide">我要入驻 · 看教程</a></div>';
    }
    list.innerHTML = html;
    var r = document.getElementById('svcRetry');
    if (r) r.onclick = function(){ load(cat); };
  }

  var current = catFromUrl();
  if (current) load(current);
  else { mark(''); list.hidden = true; }
})();
