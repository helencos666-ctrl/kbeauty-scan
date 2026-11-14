/* 旅韩助手：地图跳转（Naver 地图为主，高德地图为辅），只用公开网页链接，无需登录或密钥 */
var LHMap = (function(){
  var SRC = 'lvhanzhushou', _st = null;
  function enc(s){ return encodeURIComponent(s); }
  function isMobile(){ return /Android|iPhone|iPad|iPod|Mobile|HarmonyOS/i.test(navigator.userAgent || ''); }
  // 站名 → [经度, 纬度]（WGS84，数据来自 Wikidata）
  function stations(){
    if (!_st) _st = fetch('/static/stations.json').then(function(r){ return r.json(); }).then(function(j){ return j.stations || {}; }).catch(function(){ return {}; });
    return _st;
  }
  function naverSearch(q){ return 'https://map.naver.com/p/search/' + enc(q); }
  // a/b: {name, lng, lat}
  function naverRoute(a, b){
    if (isMobile()){
      return 'https://m.map.naver.com/?slng=' + a.lng + '&slat=' + a.lat + '&sText=' + enc(a.name) +
        '&elng=' + b.lng + '&elat=' + b.lat + '&eText=' + enc(b.name) + '&menu=route&pathType=1';
    }
    function seg(p){ return p.lng + ',' + p.lat + ',' + enc(p.name) + ',,SUBWAY_STATION'; }
    return 'https://map.naver.com/p/directions/' + seg(a) + '/' + seg(b) + '/-/transit';
  }
  function amapSearch(q, c){
    return 'https://uri.amap.com/search?keyword=' + enc(q) + (c ? '&center=' + c[0] + ',' + c[1] : '') +
      '&coordinate=wgs84&callnative=1&src=' + SRC;
  }
  function amapRoute(a, b){
    return 'https://uri.amap.com/navigation?from=' + a.lng + ',' + a.lat + ',' + enc(a.name) + '&to=' + b.lng + ',' + b.lat + ',' + enc(b.name) +
      '&mode=bus&coordinate=wgs84&callnative=1&src=' + SRC;
  }
  function buttons(naverUrl, amapUrl){
    function esc(s){ return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }
    return '<div class="lhmap"><a class="lhmap-naver" href="' + esc(naverUrl) + '" target="_blank" rel="noopener">Naver 地图（推荐）</a>' +
      '<a class="lhmap-amap" href="' + esc(amapUrl) + '" target="_blank" rel="noopener">高德地图</a></div>';
  }
  return {isMobile: isMobile, stations: stations, naverSearch: naverSearch, naverRoute: naverRoute, amapSearch: amapSearch, amapRoute: amapRoute, buttons: buttons};
})();
