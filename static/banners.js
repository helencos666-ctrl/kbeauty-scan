/* 横幅广告位：<div data-banner-slot="board:hotel" data-banner-area=""></div>
   没有生效中的横幅时整个区域隐藏，不留空位；只跳转站内页面（/go/banner/ID 由服务器换成站内路径） */
(function(){
  var CSS = '.bnw{margin:0 0 12px;position:relative}.bnw[hidden]{display:none!important}' +
    '.bnt{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;gap:10px;scrollbar-width:none;border-radius:16px}.bnt::-webkit-scrollbar{display:none}' +
    '.bns{flex:0 0 100%;scroll-snap-align:start;position:relative;border-radius:16px;overflow:hidden;background:#f3f4f6;aspect-ratio:21/9;display:block;color:#fff;text-decoration:none}' +
    '.bns img{width:100%;height:100%;object-fit:cover;display:block}' +
    '.bns .bx{position:absolute;left:0;right:0;bottom:0;padding:22px 12px 10px;background:linear-gradient(transparent,rgba(0,0,0,.62))}' +
    '.bns b{display:block;font-size:16px;line-height:1.3}.bns small{display:block;font-size:12.5px;opacity:.92;margin-top:2px}' +
    '.bns .ad{position:absolute;top:8px;left:8px;font-size:10.5px;background:rgba(0,0,0,.38);padding:1px 6px;border-radius:6px}' +
    '.bnc{position:absolute;right:10px;bottom:10px;z-index:2;background:#ff6b8f;color:#fff;font-size:12.5px;font-weight:600;padding:6px 10px;border-radius:999px;text-decoration:none}' +
    '.bnd{display:flex;justify-content:center;gap:5px;margin-top:6px}.bnd i{width:6px;height:6px;border-radius:3px;background:#d1d5db}.bnd i.on{background:#ff6b8f;width:14px}';
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function css(){ if (document.getElementById('bncss')) return; var s = document.createElement('style'); s.id = 'bncss'; s.textContent = CSS; document.head.appendChild(s); }
  async function mount(el, slot, area){
    el.classList.add('bnw'); el.hidden = true;
    if (el._bnTimer) clearInterval(el._bnTimer);
    var d;
    try {
      var r = await fetch('/api/banners?slot=' + encodeURIComponent(slot) + (area ? '&area=' + encodeURIComponent(area) : ''), {credentials: 'same-origin'});
      if (!r.ok) return; d = await r.json();
    } catch(e){ return; }
    var list = (d && d.banners) || [];
    if (!list.length){ el.innerHTML = ''; return; }
    css();
    el.innerHTML = '<div class="bnt">' + list.map(function(b){
      return '<div class="bns" style="flex:0 0 100%"><a href="' + esc(b.href) + '" style="display:block;width:100%;height:100%;color:#fff;text-decoration:none">' +
        '<img loading="lazy" alt="' + esc(b.title) + '" src="' + esc(b.img) + '"><span class="ad">推广</span>' +
        '<div class="bx"><b>' + esc(b.title) + '</b>' + (b.subtitle ? '<small>' + esc(b.subtitle) + '</small>' : '') + '</div></a>' +
        '<a class="bnc" href="' + esc(b.consult) + '">💬 立即咨询</a></div>';
    }).join('') + '</div>' + (list.length > 1 ? '<div class="bnd">' + list.map(function(_, i){ return '<i' + (i ? '' : ' class="on"') + '></i>'; }).join('') + '</div>' : '');
    el.hidden = false;
    if (list.length > 1){
      var t = el.querySelector('.bnt'), dots = el.querySelectorAll('.bnd i'), cur = 0;
      t.addEventListener('scroll', function(){ var i = Math.round(t.scrollLeft / t.clientWidth); if (i !== cur){ cur = i; dots.forEach(function(x, k){ x.className = k === i ? 'on' : ''; }); } }, {passive: true});
      el._bnTimer = setInterval(function(){ if (document.hidden) return; var n = (cur + 1) % list.length; t.scrollTo({left: n * t.clientWidth, behavior: 'smooth'}); }, 4500);
    }
  }
  window.LHBanner = {mount: mount};
  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('[data-banner-slot]').forEach(function(el){ mount(el, el.dataset.bannerSlot, el.dataset.bannerArea || ''); });
  });
})();
