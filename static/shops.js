/* 商家服务分类列表：已入驻商家（审核通过的入驻申请 + 商家会员发布的服务 + 合作商家）在前，
   分类示范案例在后。示范案例有明确标注，不是真实商家。
   点分类不再整页刷新：直接在宫格下方展开列表并滚动到列表，地址栏同步 ?cat=。 */
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
  var seq = 0;

  function catFromUrl(){
    var c = (LH.qs('cat') || '').trim();
    if (CATS[c]) return c;
    return '';
  }
  function mark(cat){
    box.querySelectorAll('[data-cat]').forEach(function(x){
      var on = !!cat && x.getAttribute('data-cat') === cat;
      x.classList.toggle('on', on);
      x.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }
  function getJson(path){
    return LH.api('GET', path);
  }
  function imgTag(src, alt){
    return '<img loading="lazy" decoding="async" alt="' + LH.esc(alt || '') + '" src="' + LH.esc(src) + '" data-hideonerr="1">';
  }
  function cardExample(ex){
    var cover = (ex.photos && ex.photos[0]) ? '<a class="mcover" href="' + LH.esc(ex.href) + '">' + imgTag(ex.photos[0].src, '示范照片（示意）') + '</a>' : '';
    return '<article class="mpost example-card" data-kind="example">' + cover + '<div class="mbody">' +
      '<span class="ex-badge">' + LH.esc(ex.badge) + '</span>' +
      '<h3><a href="' + LH.esc(ex.href) + '">' + LH.esc(ex.name) + '</a></h3>' +
      '<p class="ex">' + LH.esc(ex.intro) + '</p>' +
      '<div class="mfoot"><span class="mmeta">' + LH.esc(ex.area) + '</span>' +
      '<a class="btn ghost consult-btn" href="' + LH.esc(ex.href) + '">查看示范</a></div>' +
      '</div></article>';
  }
  function cardReal(o){
    var cover = o.photo ? '<a class="mcover" href="' + LH.esc(o.href) + '">' + imgTag(o.photo, o.name) + '</a>' : '';
    return '<article class="mpost" data-kind="' + LH.esc(o.kind) + '" data-href="' + LH.esc(o.href) + '">' + cover + '<div class="mbody">' +
      '<span class="tag">' + LH.esc(o.catName || '') + '</span>' +
      '<h3><a class="shop-link" href="' + LH.esc(o.href) + '">' + LH.esc(o.name) + '</a></h3>' +
      (o.desc ? '<p class="ex">' + LH.esc(o.desc) + '</p>' : '') +
      '<div class="mfoot"><span class="mmeta">' + LH.esc(o.meta || '') + '</span>' +
      '<a class="btn ghost consult-btn" href="' + LH.esc(o.href) + '">查看详情</a></div>' +
      '</div></article>';
  }
  function fromProvider(it){
    var n = (it.photos && it.photos.length) ? it.photos.length : (it.photo ? 1 : 0);
    return {kind: 'provider', href: '/shops/p/' + it.id, name: it.name, photo: it.photo || '',
            catName: it.category_name || CATS[it.category] || '', desc: it.services || '',
            meta: (it.area || '') + (n ? ' · ' + n + ' 张照片' : '')};
  }
  function fromPartner(it){
    return {kind: 'partner', href: it.href, name: it.name, photo: it.photo || '',
            catName: it.category_name || '', desc: it.services || '', meta: it.area || ''};
  }
  function fromPost(p){
    var name = p.title || p.offer || '商家服务';
    return {kind: 'post', href: '/community/post/' + p.id, name: name,
            photo: p.cover ? '/api/community/img/' + p.cover + '?t=1' : '',
            catName: p.merchant_cat_name || '', desc: p.excerpt || '',
            meta: [p.author || '', p.area || ''].filter(Boolean).join(' · ')};
  }

  async function fetchAll(cat){
    var q = cat ? encodeURIComponent(cat) : 'all';
    var res = await Promise.allSettled([
      getJson('/api/providers' + (cat ? '?cat=' + q : '')),
      getJson('/api/shops/partners?cat=' + q),
      getJson('/api/community/merchant-feed?limit_n=30&cat=' + q)
    ]);
    var real = [], errs = 0;
    if (res[0].status === 'fulfilled') (res[0].value.items || []).forEach(function(it){ real.push([it.category, fromProvider(it)]); }); else errs++;
    if (res[1].status === 'fulfilled') (res[1].value.items || []).forEach(function(it){ real.push([it.category, fromPartner(it)]); }); else errs++;
    if (res[2].status === 'fulfilled') (res[2].value.posts || []).forEach(function(p){ real.push([p.merchant_cat || 'other', fromPost(p)]); }); else errs++;
    return {real: real, errs: errs, total: res.length};
  }

  async function counts(){
    try {
      var r = await fetchAll('');
      var c = {};
      r.real.forEach(function(x){ if (CATS[x[0]]) c[x[0]] = (c[x[0]] || 0) + 1; });
      box.querySelectorAll('[data-cat]').forEach(function(t){
        var k = t.getAttribute('data-cat'), n = c[k] || 0;
        var el = t.querySelector('.cnt');
        if (!n){ if (el) el.remove(); return; }
        if (!el){ el = document.createElement('span'); el.className = 'cnt'; t.appendChild(el); }
        el.textContent = n;
        el.setAttribute('aria-label', '已入驻 ' + n + ' 家');
      });
    } catch(e){}
  }

  function reveal(){
    try { list.scrollIntoView({behavior: 'smooth', block: 'start'}); }
    catch(e){ list.scrollIntoView(true); }
  }

  async function load(cat, opts){
    opts = opts || {};
    var my = ++seq;
    mark(cat);
    if (!cat){
      list.innerHTML = '';
      list.hidden = true;
      if (title) title.textContent = '商家服务';
      return;
    }
    list.hidden = false;
    if (title) title.textContent = CATS[cat] || '商家服务';
    list.innerHTML = '<div class="list-head"><span><b>' + LH.esc(CATS[cat]) + '</b></span></div><div class="loading">加载中…</div>';
    if (opts.scroll) reveal();
    var exP = getJson('/api/shop-examples/' + encodeURIComponent(cat)).then(function(r){ return r.item; }, function(){ return null; });
    var r = await fetchAll(cat);
    var ex = await exP;
    if (my !== seq) return;
    var real = r.real.filter(function(x){ return x[0] === cat; }).map(function(x){ return x[1]; });
    var html = '<div class="list-head"><span><b>' + LH.esc(CATS[cat]) + '</b> · 已入驻 ' + real.length + ' 家</span>' +
      '<button type="button" id="svcAll">全部分类</button></div>';
    real.forEach(function(o){ html += cardReal(o); });
    if (r.errs === r.total){
      html += '<div class="load-err">商家列表暂时加载失败，请稍后再试' +
        '<br><button class="btn ghost" type="button" id="svcRetry">重试</button></div>';
    } else if (!real.length){
      html += '<div class="empty-state" data-empty="1"><p>「' + LH.esc(CATS[cat]) + '」还没有入驻商家，欢迎商家免费入驻。下面是填写格式示范，不是真实商家。</p>' +
        '<a class="btn primary" href="/shops/join-guide">我要入驻 · 看教程</a></div>';
    }
    if (ex){
      html += '<p class="sub-label">填写格式示范（仅供参考，非真实商家）</p>' + cardExample(ex);
    }
    list.innerHTML = html;
    var rt = document.getElementById('svcRetry');
    if (rt) rt.onclick = function(){ load(cat, {scroll: true}); };
    var all = document.getElementById('svcAll');
    if (all) all.onclick = function(){
      go('');
      try { box.scrollIntoView({behavior: 'smooth', block: 'center'}); } catch(e){}
    };
    if (opts.scroll) reveal();
  }

  function go(cat){
    var url = cat ? ('/shops?cat=' + encodeURIComponent(cat)) : '/shops';
    try { history.pushState({cat: cat}, '', url); } catch(e){}
    return load(cat, {scroll: !!cat});
  }

  box.addEventListener('click', function(ev){
    var a = ev.target.closest ? ev.target.closest('[data-cat]') : null;
    if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
    var c = a.getAttribute('data-cat');
    if (!CATS[c]) return;
    ev.preventDefault();
    go(c);
  });
  window.addEventListener('popstate', function(){ load(catFromUrl(), {scroll: !!catFromUrl()}); });

  var current = catFromUrl();
  if (current) load(current, {scroll: true});
  else { mark(''); list.hidden = true; }
  counts();
})();
