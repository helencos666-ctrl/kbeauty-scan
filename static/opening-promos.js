/* 开业活动：读取 JSON 配置，控制「我的」红标、首页每日弹层和活动列表。 */
(function(w){
  var CFG_URL = '/static/opening-promos.json?v=98';
  var cache = null, inflight = null;

  function kstDay(d){
    d = d || new Date();
    return new Date(d.getTime() + 9 * 3600e3).toISOString().slice(0, 10);
  }
  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function load(){
    if (cache) return Promise.resolve(cache);
    if (inflight) return inflight;
    inflight = fetch(CFG_URL, {credentials: 'same-origin'}).then(function(r){
      if (!r.ok) throw new Error('promo cfg');
      return r.json();
    }).then(function(j){ cache = j; inflight = null; return j; })
      .catch(function(){ inflight = null; return null; });
    return inflight;
  }
  function isActive(cfg){
    if (!cfg || cfg.enabled === false) return false;
    var day = kstDay();
    if (cfg.starts && day < cfg.starts) return false;
    if (cfg.ends && day > cfg.ends) return false;
    return true;
  }
  function visibleItems(cfg){
    return ((cfg && cfg.items) || []).filter(function(it){ return it && it.enabled !== false; });
  }
  function paintMeTab(cfg){
    var on = isActive(cfg);
    if (document.body) document.body.classList.toggle('lh-opening', on);
    var a = document.querySelector('nav.tabbar a[data-nav="me"]');
    if (!a) return;
    var b = a.querySelector('.tab-hot');
    if (!on){
      if (b) b.remove();
      return;
    }
    if (!b){
      b = document.createElement('span');
      b.className = 'tab-hot';
      b.textContent = '开';
      a.appendChild(b);
    }
  }
  function cardHTML(it, compact){
    var note = (!compact && it.note) ? '<p class="op-note">' + esc(it.note) + '</p>' : '';
    var href = it.href || '';
    var cta = it.cta || '查看详情';
    var body = compact ? (it.summary || it.body || '') : (it.body || it.summary || '');
    var link = href ? '<a class="op-cta" href="' + esc(href) + '">' + esc(cta) + ' ›</a>' : '';
    return '<article class="op-card" id="' + esc(it.id || '') + '">' +
      '<span class="op-ico">' + esc(it.icon || '🎉') + '</span>' +
      '<div class="op-bd"><h3>' + esc(it.title || '') + '</h3>' +
      '<p>' + esc(body) + '</p>' + note + link + '</div></article>';
  }
  function renderList(el, cfg, compact){
    if (!el) return;
    var items = visibleItems(cfg);
    if (!items.length){
      el.innerHTML = '<p class="tip">当前没有进行中的开业活动。</p>';
      return;
    }
    el.innerHTML = items.map(function(it){ return cardHTML(it, compact); }).join('');
  }

  function injectSheet(){
    if (document.getElementById('openPromoSheet')) return document.getElementById('openPromoSheet');
    var wrap = document.createElement('div');
    wrap.id = 'openPromoSheet';
    wrap.className = 'sheet op-sheet';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.setAttribute('aria-labelledby', 'opSheetT');
    wrap.hidden = true;
    wrap.innerHTML = '<div class="sheet-in op-sheet-in">' +
      '<div class="sheet-h"><b id="opSheetT">开业活动</b>' +
      '<button type="button" class="x" id="opSheetX" aria-label="关闭">✕</button></div>' +
      '<div id="opSheetList" class="op-list"></div>' +
      '<a class="op-all" href="/opening">查看全部活动 ›</a>' +
      '<p class="op-foot">店内优惠以店家现场规则为准。分享这个动作本身没有奖励，邀请只计一级。</p>' +
      '</div>';
    document.body.appendChild(wrap);
    function dismiss(){
      wrap.hidden = true;
      document.body.classList.remove('lh-overlay-open');
      try {
        var key = (cache && cache.storage_key) || 'lh_opening_popup_day';
        localStorage.setItem(key, kstDay());
      } catch (e) {}
    }
    wrap.addEventListener('click', function(e){ if (e.target === wrap) dismiss(); });
    var x = document.getElementById('opSheetX');
    if (x) x.addEventListener('click', dismiss);
    return wrap;
  }

  function onHome(){
    var p = location.pathname;
    if (!(p === '/' || p === '/index.html' || p === '/static/index.html')) return false;
    var tab = new URLSearchParams(location.search || '').get('tab');
    return !tab || tab === 'home';
  }

  function splashGone(){
    var sp = document.getElementById('splash');
    if (!sp) return true;
    if (sp.hidden) return true;
    var st = window.getComputedStyle(sp);
    return st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0;
  }

  function maybePopup(cfg){
    if (!onHome() || !isActive(cfg) || !visibleItems(cfg).length) return;
    var key = cfg.storage_key || 'lh_opening_popup_day';
    try { if (localStorage.getItem(key) === kstDay()) return; } catch (e) {}
    var tries = 0;
    (function wait(){
      if (!splashGone() && tries++ < 40){ setTimeout(wait, 200); return; }
      var sheet = injectSheet();
      var t = document.getElementById('opSheetT');
      if (t) t.textContent = cfg.popup_title || cfg.title || '开业活动';
      renderList(document.getElementById('opSheetList'), cfg, true);
      sheet.hidden = false;
      document.body.classList.add('lh-overlay-open');
    })();
  }

  function boot(){
    load().then(function(cfg){
      if (!cfg) return;
      paintMeTab(cfg);
      maybePopup(cfg);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
  w.addEventListener('pageshow', function(){ if (cache) paintMeTab(cache); else boot(); });

  w.LHOpening = {
    load: load,
    isActive: isActive,
    visibleItems: visibleItems,
    renderList: renderList,
    paintMeTab: paintMeTab,
    esc: esc,
    kstDay: kstDay
  };
})(window);
