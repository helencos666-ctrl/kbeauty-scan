/* 旅韩助手：首都圈地铁站内路线计算（纯前端，离线可用）
 * 数据：/static/subway.json?v=92，由 scripts/build_subway.py 生成
 * 来源：© OpenStreetMap contributors（ODbL 1.0）线路与停靠顺序；Wikidata（CC0）中文站名补充。
 * 时间为按站间距离估算，票价按首尔交通公社公布的距离制规则估算（基本 1,550 韩元/10km，交通卡），均仅供参考。
 */
var LHSub = (function(){
  var D = null, P = null;
  var TRANSFER_MIN = 5, TRANSFER_PENALTY = 3;
  // 另行计价的线路：不估算票价
  var EXTRA_FARE = {'공항철도':1,'신분당':1,'용인':1,'U':1,'W':1,'김포 골드라인':1,'경강':0,'서해':1};
  // 站距较长的近郊/机场线，平均车速较快
  var FAST = {'공항철도':1,'경의·중앙':1,'경춘':1,'수인·분당':1,'서해':1,'경강':1,'1':0};
  function load(){
    if (!P) P = fetch('/static/subway.json?v=92').then(function(r){ if(!r.ok) throw new Error('load'); return r.json(); }).then(function(j){ D = prep(j); return D; });
    P.catch(function(){ P = null; });
    return P;
  }
  function norm(s){
    return String(s||'').trim().toLowerCase()
      .replace(/[\s.\-_'’·,，。()（）]/g,'')
      .replace(/(地铁站|火车站|车站|地铁|站)$/,'')
      .replace(/역$/,'').replace(/station$/,'').replace(/stn$/,'');
  }
  function hav(a,b){
    var R=6371, r=Math.PI/180, dLa=(b[1]-a[1])*r, dLo=(b[0]-a[0])*r;
    var x=Math.sin(dLa/2)*Math.sin(dLa/2)+Math.cos(a[1]*r)*Math.cos(b[1]*r)*Math.sin(dLo/2)*Math.sin(dLo/2);
    return 2*R*Math.asin(Math.sqrt(x));
  }
  // 前缀索引：norm(key) 的前 1–2 个字符（以及完整 key）-> 站索引数组
  function addIdx(ix, k, i){
    if (!k) return;
    var a = ix[k]; if (!a) a = ix[k] = [];
    if (a[a.length-1] !== i) a.push(i);
  }
  function prep(j){
    var st = j.st.map(function(s,i){ return {i:i, ko:s[0], zh:s[1], en:s[2], p:[s[3],s[4]], lines:s[5], keys:s[6]}; });
    var lines = j.lines.map(function(l){ return {id:l[0], zh:l[1], ko:l[2], color:l[3]}; });
    var lineIdx = {}; lines.forEach(function(l,i){ lineIdx[l.id]=i; });
    var out = st.map(function(){ return []; });
    j.e.forEach(function(e){
      var km = hav(st[e[0]].p, st[e[1]].p);
      var fast = FAST[lines[e[2]].id];
      out[e[0]].push({to:e[1], line:e[2], loop:e[3]||'', km:km, min:Math.max(1.5, 0.5 + km*(fast?0.9:1.25))});
    });
    var nameCount = {}; st.forEach(function(s){ nameCount[s.ko]=(nameCount[s.ko]||0)+1; });
    // 每个站已归一化的 key 列表（search 时不必再 norm），并建前缀索引
    var nkeys = st.map(function(s){
      var ks = [], seen = {};
      for (var k=0;k<s.keys.length;k++){
        var kk = norm(s.keys[k]);
        if (kk && !seen[kk]){ seen[kk]=1; ks.push(kk); }
      }
      return ks;
    });
    var idx = {};
    nkeys.forEach(function(ks, i){
      for (var k=0;k<ks.length;k++){
        var key = ks[k];
        addIdx(idx, key, i);
        addIdx(idx, key.slice(0,1), i);
        if (key.length >= 2) addIdx(idx, key.slice(0,2), i);
      }
    });
    return {st:st, lines:lines, lineIdx:lineIdx, out:out, nameCount:nameCount, nkeys:nkeys, idx:idx, source:j.source};
  }
  function lev(a,b){
    if (Math.abs(a.length-b.length)>2) return 9;
    var m=[],i,j; for(i=0;i<=b.length;i++) m[i]=[i];
    for(j=0;j<=a.length;j++) m[0][j]=j;
    for(i=1;i<=b.length;i++) for(j=1;j<=a.length;j++)
      m[i][j]=Math.min(m[i-1][j]+1,m[i][j-1]+1,m[i-1][j-1]+(b.charAt(i-1)===a.charAt(j-1)?0:1));
    return m[b.length][a.length];
  }
  // 取候选站索引：完整 key / 前 1–2 个字符的前缀桶
  function candidates(n){
    var ix = D.idx, set = {}, list = [];
    function take(bucket){
      var a = bucket && ix[bucket]; if (!a) return;
      for (var i=0;i<a.length;i++){ var s = a[i]; if (!set[s]){ set[s]=1; list.push(s); } }
    }
    take(n);
    take(n.slice(0,1));
    if (n.length >= 2) take(n.slice(0,2));
    return list;
  }
  function score(s, n){
    var best = 0, ks = D.nkeys[s.i];
    for (var k=0;k<ks.length;k++){
      var key = ks[k]; if (!key) continue;
      var sc = 0;
      if (key===n) sc = 100;
      else if (key.indexOf(n)===0) sc = 80 - Math.min(20, key.length-n.length);
      else if (n.length>=2 && key.indexOf(n)>0) sc = 55;
      if (sc>best) best = sc;
    }
    return best;
  }
  // 返回按匹配度排序的车站
  function search(q, limit){
    if (!D) return [];
    var n = norm(q); if (!n) return [];
    var latin = /^[a-z0-9]+$/.test(n), res = [];
    var cands = candidates(n);
    for (var c=0;c<cands.length;c++){
      var s = D.st[cands[c]], best = score(s, n);
      if (best) res.push({s:s, sc:best + Math.min(6, s.lines.length)});
    }
    // 拉丁字母查询且前缀候选不够时：只对“库内存在 n 的前缀候选”这一类做编辑距离
    if (latin && n.length>=4 && res.length < (limit||8)){
      var lim = n.length>=7 ? 2 : 1, extra = {};
      for (var c2=0;c2<cands.length;c2++) extra[cands[c2]] = 1;
      var ks = D.idx[n.slice(0,2)] || [];
      for (var t=0;t<ks.length;t++){
        var si = ks[t]; if (extra[si]) continue;
        var keys = D.nkeys[si], hit = 0;
        for (var k=0;k<keys.length;k++){ if (lev(n, keys[k]) <= lim){ hit = 1; break; } }
        if (hit){ extra[si] = 1; res.push({s:D.st[si], sc:45 + Math.min(6, D.st[si].lines.length)}); }
      }
      res.sort(function(a,b){ return b.sc-a.sc; });
    } else {
      res.sort(function(a,b){ return b.sc-a.sc; });
    }
    return res.slice(0, limit||8).map(function(r){ return r.s; });
  }
  function byKo(ko){
    if (!D) return null; ko = String(ko||'').replace(/역$/,'');
    for (var i=0;i<D.st.length;i++) if (D.st[i].ko===ko) return D.st[i];
    return null;
  }
  // Dijkstra：状态 = (车站, 线路)
  function route(a, b){
    var S = D.st.length, L = D.lines.length, INF = 1e9;
    var dist = {}, prev = {}, done = {}, heap = [];
    function key(s,l){ return s*64+l; }
    function push(k,d){ heap.push([d,k]); var i=heap.length-1; while(i>0){ var p=(i-1)>>1; if(heap[p][0]<=heap[i][0]) break; var t=heap[p]; heap[p]=heap[i]; heap[i]=t; i=p; } }
    function pop(){ var top=heap[0], last=heap.pop(); if(heap.length){ heap[0]=last; var i=0; for(;;){ var l=2*i+1,r=l+1,m=i; if(l<heap.length&&heap[l][0]<heap[m][0]) m=l; if(r<heap.length&&heap[r][0]<heap[m][0]) m=r; if(m===i) break; var t=heap[m]; heap[m]=heap[i]; heap[i]=t; i=m; } } return top; }
    D.st[a].lines.forEach(function(lid){ var k=key(a,D.lineIdx[lid]); dist[k]=0; push(k,0); });
    var end = -1;
    while (heap.length){
      var it = pop(), d = it[0], k = it[1];
      if (done[k]) continue; done[k] = 1;
      var s = Math.floor(k/64), l = k%64;
      if (s===b){ end = k; break; }
      D.out[s].forEach(function(e){
        var nk, nd;
        if (e.line===l){ nk = key(e.to,l); nd = d + e.min; }
        else { nk = key(e.to,e.line); nd = d + e.min + TRANSFER_MIN + TRANSFER_PENALTY; }
        if (nd < (dist[nk]===undefined?INF:dist[nk])){ dist[nk]=nd; prev[nk]={k:k, e:e, xfer:e.line!==l}; push(nk,nd); }
      });
    }
    if (end<0) return null;
    var hops = []; var k2 = end;
    while (prev[k2]){ hops.unshift({from:Math.floor(prev[k2].k/64), to:Math.floor(k2/64), e:prev[k2].e}); k2 = prev[k2].k; }
    var legs = [];
    hops.forEach(function(h){
      var last = legs[legs.length-1];
      if (last && last.line===h.e.line){ last.stops.push(h.to); last.min+=h.e.min; last.km+=h.e.km; }
      else legs.push({line:h.e.line, from:h.from, stops:[h.to], min:h.e.min, km:h.e.km, loop:h.e.loop, next:h.to});
    });
    legs.forEach(function(lg){ lg.to = lg.stops[lg.stops.length-1]; lg.term = terminus(lg); });
    var min = legs.reduce(function(t,l){ return t+l.min; },0) + TRANSFER_MIN*(legs.length-1);
    var km = legs.reduce(function(t,l){ return t+l.km; },0);
    return {legs:legs, min:Math.round(min), km:km, stops:legs.reduce(function(t,l){ return t+l.stops.length; },0)};
  }
  // 沿行车方向一直走到线路尽头；遇到分岔就不给终点（避免说错方向）
  function terminus(lg){
    var stops = [lg.from].concat(lg.stops), prevS = stops[stops.length-2], cur = lg.to, seen = {}, guard = 0;
    seen[cur]=1; stops.forEach(function(s){ seen[s]=1; });
    while (guard++ < 200){
      var nx = D.out[cur].filter(function(e){ return e.line===lg.line && e.to!==prevS; });
      if (nx.length===0) return cur===lg.to ? lg.to : cur;
      if (nx.length>1) return -1;
      if (seen[nx[0].to]) return -1; // 环线
      seen[nx[0].to]=1; prevS = cur; cur = nx[0].to;
    }
    return -1;
  }
  function fare(r){
    for (var i=0;i<r.legs.length;i++) if (EXTRA_FARE[D.lines[r.legs[i].line].id]) return null;
    var km = r.km * 1.15; // 直线距离换算为线路距离的粗略系数
    var f = 1550;
    if (km>10) f += Math.ceil(Math.min(km,50)-10>0 ? (Math.min(km,50)-10)/5 : 0)*100;
    if (km>50) f += Math.ceil((km-50)/8)*100;
    return f;
  }
  function label(s){ return (s.zh && s.zh!==s.ko ? s.zh : (s.en || s.ko)) + (s.zh?'站':''); }
  return {load:load, search:search, byKo:byKo, route:route, fare:fare, label:label, norm:norm, data:function(){ return D; }};
})();
