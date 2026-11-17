/* 管理后台扩展：横幅广告、站内咨询、运营（官方号/未回复/徽章）、联系方式拦截记录；帖子置顶/加精/话题
   依赖 admin.html 里的全局 call / msg / E */
(function(){
  function $(id){ return document.getElementById(id); }
  var BM = null;
  function opt(list, cur){ return list.map(function(x){ return '<option value="' + E(x[0]) + '"' + (String(x[0]) === String(cur) ? ' selected' : '') + '>' + E(x[1]) + '</option>'; }).join(''); }

  // ---------- 横幅广告 ----------
  window.bannersAdmin = async function(){
    var d = await call('GET', '/api/admin/banners?slot=' + encodeURIComponent(($('bnFilter') || {}).value || ''));
    BM = d;
    var slots = d.slots.map(function(s){ return [s.key, s.name]; });
    var box = $('t-banners');
    if (!$('bnForm')){
      box.innerHTML = '<div class="card"><h2>横幅广告位</h2><p class="tip" style="margin-top:0">图片建议 21:9（如 1260×540）。跳转只能是站内页面：推荐店铺详情、炸鸡店、商家活动帖或站内咨询。到期自动不再显示；没有生效横幅的页面不留空位。<b>饭店横幅</b>：广告位选「美食页」，跳转选「推荐店铺详情」并选店铺。</p>' +
        '<div id="bnForm"></div></div><div class="card"><div class="row" style="gap:8px;margin-bottom:8px"><select class="f" id="bnFilter" style="flex:1"><option value="">全部广告位</option>' + opt(slots, '') + '</select><button class="btn primary" id="bnRef">刷新</button></div><div id="bnList"></div></div>';
      $('bnRef').onclick = bannersAdmin;
      $('bnFilter').onchange = bannersAdmin;
      drawForm(slots);
    }
    $('bnList').innerHTML = d.banners.length ? d.banners.map(function(b){
      var st = !b.active ? '<b style="color:#999">已停用</b>' : (b.expired ? '<b style="color:#c62828">已过期</b>' : (b.live ? '<b style="color:#1B7A45">展示中</b>' : '<b style="color:#e65100">未到开始日期/关联已下架</b>'));
      return '<div class="cmt" style="display:block"><div class="row" style="gap:10px;align-items:flex-start"><img src="' + b.img + '" alt="" style="width:150px;border-radius:8px">' +
        '<div style="flex:1;min-width:0"><div class="who">#' + b.id + ' ' + E(b.title) + ' · ' + st + '</div><div class="ct" style="color:var(--sub)">' + E(slotName(b.slot)) + ' · ' + E(BM.link_kinds[b.link_kind] || b.link_kind) + (b.link_id ? ' #' + b.link_id : '') +
        (b.subtitle ? ' · ' + E(b.subtitle) : '') + '<br>排序 ' + b.sort + ' · ' + (b.starts_on || '不限') + ' ~ ' + (b.ends_on || '不限') + ' · 报价 ¥' + b.price + (b.paid ? '（已付）' : '（未付）') + ' · 点击 ' + b.clicks + (b.merchant_no ? ' · 商家编号 ' + b.merchant_no : '') + '</div>' +
        '<div class="row" style="margin-top:6px;flex-wrap:wrap"><button class="btn sm gray" data-bn="' + b.id + '" data-act="' + (b.active ? 'disable">停用' : 'enable">启用') + '</button>' +
        '<button class="btn sm gray" data-bn="' + b.id + '" data-act="' + (b.paid ? 'unpaid">标为未付' : 'paid">标为已付') + '</button>' +
        '<button class="btn sm gray" data-bned="' + b.id + '">改日期/排序/报价</button><button class="btn sm gray" style="color:#c62828" data-bn="' + b.id + '" data-act="delete">删除</button></div></div></div></div>';
    }).join('') : '<div class="empty">还没有横幅</div>';
    document.querySelectorAll('#bnList [data-bn]').forEach(function(x){ x.onclick = async function(){
      if (x.dataset.act === 'delete' && !confirm('删除横幅 #' + x.dataset.bn + '？')) return;
      await call('POST', '/api/admin/banners/' + x.dataset.bn, {action: x.dataset.act}); bannersAdmin();
    }; });
    document.querySelectorAll('#bnList [data-bned]').forEach(function(x){ x.onclick = async function(){
      var b = BM.banners.filter(function(y){ return String(y.id) === x.dataset.bned; })[0];
      var s = prompt('开始日期（YYYY-MM-DD，留空不限）', b.starts_on); if (s === null) return;
      var e = prompt('结束日期（YYYY-MM-DD，留空不限）', b.ends_on); if (e === null) return;
      var so = prompt('排序（数字越小越靠前）', b.sort); if (so === null) return;
      var pr = prompt('报价（元）', b.price); if (pr === null) return;
      try { await call('POST', '/api/admin/banners/' + b.id, {starts_on: s, ends_on: e, sort: so, price: pr}); msg('已保存', false); bannersAdmin(); } catch(err){}
    }; });
  };
  function slotName(k){ var s = (BM.slots || []).filter(function(x){ return x.key === k; })[0]; return s ? s.name : k; }
  function drawForm(slots){
    var f = $('bnForm');
    f.innerHTML = '<label class="f">广告位</label><select class="f" id="bf_slot">' + opt(slots, 'food') + '</select>' +
      '<label class="f">图片</label><input type="file" id="bf_img" accept="image/jpeg,image/png,image/webp">' +
      '<label class="f">标题（2–30 字）</label><input class="f" id="bf_title" maxlength="30" placeholder="例如：明洞烤肉 · 中文菜单">' +
      '<label class="f">副标 / 优惠（选填）</label><input class="f" id="bf_sub" maxlength="40" placeholder="例如：凭旅韩助手 9 折">' +
      '<label class="f">点击跳转</label><select class="f" id="bf_kind">' + opt(Object.keys(BM.link_kinds).map(function(k){ return [k, BM.link_kinds[k]]; }), 'shop') + '</select>' +
      '<div id="bf_target"></div>' +
      '<label class="f">商家会员编号（选填：咨询发给该商家；不填则由平台客服接待）</label><input class="f" id="bf_mer" placeholder="会员编号">' +
      '<label style="font-size:13px;display:block;margin-top:6px"><input type="checkbox" id="bf_bind"> 同时把关联店铺绑定给该商家（店铺详情页的「立即咨询」也发给他）</label>' +
      '<div class="row" style="gap:8px"><div style="flex:1"><label class="f">开始日期</label><input class="f" type="date" id="bf_s"></div><div style="flex:1"><label class="f">结束日期</label><input class="f" type="date" id="bf_e"></div></div>' +
      '<div class="row" style="gap:8px"><div style="flex:1"><label class="f">排序</label><input class="f" id="bf_sort" value="0"></div><div style="flex:1"><label class="f">报价（元，商家付款用）</label><input class="f" id="bf_price" value="0"></div></div>' +
      '<button class="btn primary block" id="bf_go" style="margin-top:10px">创建横幅</button>';
    function target(){
      var k = $('bf_kind').value, t = $('bf_target');
      if (k === 'shop') t.innerHTML = '<label class="f">选择推荐店铺</label><select class="f" id="bf_lid">' + opt(BM.shops.map(function(s){ return [s.id, '#' + s.id + ' ' + s.name + ' · ' + s.area + ' · ' + s.type + (s.owner_id ? '（已绑定商家）' : '')]; }), '') + '</select>';
      else if (k === 'chicken') t.innerHTML = '<label class="f">选择炸鸡店</label><select class="f" id="bf_lid">' + opt(BM.chicken_shops.map(function(s){ return [s.id, '#' + s.id + ' ' + s.name + (s.area ? ' · ' + s.area : '')]; }), '') + '</select>';
      else if (k === 'post') t.innerHTML = '<label class="f">活动帖编号</label><input class="f" id="bf_lid" placeholder="帖子 #编号">';
      else t.innerHTML = '<div class="tip">点击后直接打开站内咨询</div>';
    }
    $('bf_kind').onchange = target; target();
    $('bf_go').onclick = async function(){
      var file = $('bf_img').files[0]; if (!file){ msg('请选择横幅图片', true); return; }
      var fd = new FormData();
      fd.append('slot', $('bf_slot').value); fd.append('title', $('bf_title').value); fd.append('subtitle', $('bf_sub').value);
      fd.append('link_kind', $('bf_kind').value); fd.append('link_id', ($('bf_lid') || {}).value || '');
      fd.append('merchant', $('bf_mer').value); fd.append('bind_owner', $('bf_bind').checked ? '1' : '');
      fd.append('starts_on', $('bf_s').value); fd.append('ends_on', $('bf_e').value); fd.append('sort', $('bf_sort').value); fd.append('price', $('bf_price').value);
      fd.append('image', await LH.compress(file), 'banner.jpg');
      this.disabled = true;
      try { var r = await call('POST', '/api/admin/banners', fd); msg('已创建横幅 #' + r.id, false); $('bf_title').value = ''; $('bf_sub').value = ''; $('bf_img').value = ''; bannersAdmin(); } catch(e){}
      this.disabled = false;
    };
  }

  // ---------- 站内咨询 ----------
  window.consultAdmin = async function(){
    var box = $('t-consult');
    if (!$('csList')) box.innerHTML = '<div class="card"><h2>站内咨询</h2><p class="tip" style="margin-top:0">「平台客服」会话由管理员直接回复；用户与商家的会话只能查看（用于举报核查）。</p>' +
      '<div class="row" style="gap:8px;margin-bottom:8px"><select class="f" id="csF" style="flex:1"><option value="1">只看平台客服</option><option value="0">全部会话</option></select><button class="btn primary" id="csR">刷新</button></div><div id="csList"></div><div id="csView"></div></div>';
    $('csR').onclick = consultAdmin; $('csF').onchange = consultAdmin;
    var d = await call('GET', '/api/admin/consult/threads?platform=' + $('csF').value);
    $('csList').innerHTML = d.threads.length ? d.threads.map(function(t){
      return '<div class="cmt" style="display:block"><div class="who">#' + t.id + ' ' + E(t.user) + '（' + t.user_no + '）→ ' + E(t.merchant) + (t.merchant_no ? '（' + t.merchant_no + '）' : '') + (t.unread ? ' <b style="color:#c62828">' + t.unread + ' 未读</b>' : '') + '</div>' +
        '<div class="ct" style="color:var(--sub)">' + E(t.ref) + ' · ' + E(t.last) + ' · ' + E(t.time) + '</div><button class="btn sm ghost" data-cs="' + t.id + '">查看' + (t.platform ? ' / 回复' : '') + '</button></div>';
    }).join('') : '<div class="empty">没有会话</div>';
    document.querySelectorAll('#csList [data-cs]').forEach(function(b){ b.onclick = function(){ viewThread(+b.dataset.cs); }; });
  };
  async function viewThread(id){
    var d = await call('GET', '/api/admin/consult/threads/' + id), v = $('csView');
    v.innerHTML = '<h3>会话 #' + id + ' · ' + E(d.ref) + '</h3>' + d.messages.map(function(m){
      return '<div style="font-size:13px;padding:6px 0 6px 10px;border-left:3px solid ' + (m.sender === 'user' ? '#C5E0DC' : '#cfe3ff') + ';margin-top:6px"><b>' + ({user: '用户', merchant: d.platform ? '客服' : '商家', system: '系统'}[m.sender] || m.sender) + '</b>：' + E(m.body) +
        (m.img ? ' <a href="' + m.img + '" target="_blank" rel="noopener">[图片]</a>' : '') + ' <small style="color:#999">' + E(m.time) + '</small></div>';
    }).join('') + (d.platform ? '<textarea class="f" id="csTxt" maxlength="500" placeholder="以「旅韩助手客服」身份回复" style="margin-top:8px"></textarea><button class="btn primary" id="csSend" style="margin-top:6px">回复</button>' : '');
    if (d.platform) $('csSend').onclick = async function(){ try { await call('POST', '/api/admin/consult/threads/' + id + '/messages', {body: $('csTxt').value}); viewThread(id); } catch(e){} };
    v.scrollIntoView({behavior: 'smooth'});
  }

  // ---------- 运营：官方号发帖、未回复帖子、徽章 ----------
  window.opsAdmin = async function(){
    var box = $('t-ops');
    if (!$('opBoard')){
      box.innerHTML = '<div class="card"><h2>官方号发帖（韩友圈小编）</h2><p class="tip" style="margin-top:0">每周 1–2 个具体话题，勾选「本周话题」会出现在社区首页顶部；勾选「置顶」会钉在频道和首页顶部。官方号帖子自动带「官方」标识。</p>' +
        '<select class="f" id="opBoard"></select><input class="f" id="opTitle" maxlength="40" placeholder="话题标题，例如：这周生活费花在哪了？" style="margin-top:8px">' +
        '<textarea class="f" id="opBody" maxlength="3000" placeholder="话题说明和示范回答" style="margin-top:8px;min-height:100px"></textarea>' +
        '<label style="font-size:13px;margin-right:12px"><input type="checkbox" id="opTopic" checked> 本周话题</label><label style="font-size:13px"><input type="checkbox" id="opPin"> 置顶</label>' +
        '<button class="btn primary block" id="opGo" style="margin-top:8px">发布</button></div>' +
        '<div class="card"><h2>还没人回复的新帖（近 7 天）</h2><p class="tip" style="margin-top:0">以官方号逐条做第一条回复，让新人发帖不冷场。</p><div id="opUn"></div></div>' +
        '<div class="card"><h2>徽章</h2><p class="tip" style="margin-top:0">首帖、笔耕不辍、精选作者、连续签到、热心回答、在韩达人为自动授予；官方认证、优质商家由管理员手动授予。获得时用户会收到站内通知。</p>' +
        '<div class="row" style="gap:8px"><input class="f" id="bgU" placeholder="会员编号" style="flex:1"><button class="btn primary" id="bgQ">查询</button></div><div id="bgBox"></div></div>';
      var me = await LH.me().catch(function(){ return {boards: {}}; });
      var boards = me.boards || {}, mb = me.merchant_boards || [];
      $('opBoard').innerHTML = opt(Object.keys(boards).filter(function(k){ return mb.indexOf(k) < 0; }).map(function(k){ return [k, boards[k]]; }), 'daily');
      $('opGo').onclick = async function(){
        try { var r = await call('POST', '/api/admin/official-post', {board: $('opBoard').value, title: $('opTitle').value, body: $('opBody').value, topic: $('opTopic').checked, pinned: $('opPin').checked});
          msg('已发布 #' + r.id, false); $('opTitle').value = ''; $('opBody').value = ''; } catch(e){}
      };
      $('bgQ').onclick = badgeQuery;
    }
    var d = await call('GET', '/api/admin/unanswered');
    $('opUn').innerHTML = d.posts.length ? d.posts.map(function(p){
      return '<div class="cmt" style="display:block"><div class="who">#' + p.id + ' [' + E(p.board_name) + '] ' + E(p.title) + '</div><div class="ct" style="color:var(--sub)">' + E(p.author) + ' · ' + E(p.time) + ' · ' + E(p.excerpt) + '</div>' +
        '<div class="row" style="gap:6px;margin-top:6px"><input class="f" data-ocb="' + p.id + '" placeholder="官方回复…" style="flex:1"><button class="btn sm primary" data-oc="' + p.id + '">回复</button><a class="btn sm ghost" target="_blank" href="/community/post/' + p.id + '">查看</a></div></div>';
    }).join('') : '<div class="empty">近 7 天的新帖都有人回复了 👍</div>';
    document.querySelectorAll('#opUn [data-oc]').forEach(function(b){ b.onclick = async function(){
      var t = document.querySelector('[data-ocb="' + b.dataset.oc + '"]').value;
      try { await call('POST', '/api/admin/official-comment', {post_id: +b.dataset.oc, body: t}); msg('已回复 #' + b.dataset.oc, false); opsAdmin(); } catch(e){}
    }; });
  };
  async function badgeQuery(){
    var d = await call('GET', '/api/admin/badges?user=' + encodeURIComponent($('bgU').value)), u = d.user;
    if (!u){ $('bgBox').innerHTML = ''; return; }
    var have = {}; u.badges.forEach(function(b){ have[b.key] = b; });
    $('bgBox').innerHTML = '<p><b>' + E(u.nickname) + '</b>（编号 ' + u.member_no + '）' + (u.official ? ' <span class="offbadge">官方</span>' : '') + '</p>' + Object.keys(d.badges).map(function(k){
      var b = d.badges[k], h = have[k];
      return '<div class="row" style="justify-content:space-between;padding:6px 0;border-bottom:1px solid #f0f0f0"><span>' + b.icon + ' ' + E(b.name) + ' <small style="color:#999">' + (b.auto ? '自动' : '手动') + (h ? ' · ' + E(h.time) : '') + '</small></span>' +
        (h ? '<button class="btn sm gray" data-bg="' + k + '" data-act="revoke">收回</button>' : '<button class="btn sm primary" data-bg="' + k + '" data-act="grant">授予</button>') + '</div>';
    }).join('');
    document.querySelectorAll('#bgBox [data-bg]').forEach(function(b){ b.onclick = async function(){
      try { await call('POST', '/api/admin/badges', {user: $('bgU').value, badge: b.dataset.bg, action: b.dataset.act}); badgeQuery(); } catch(e){}
    }; });
  }

  // ---------- 联系方式拦截记录 ----------
  window.hitsAdmin = async function(){
    var d = await call('GET', '/api/admin/contact-hits');
    $('t-hits').innerHTML = '<div class="card"><h2>站外联系方式拦截记录</h2><p class="tip" style="margin-top:0">商家资料、活动帖、横幅、站内咨询里检测到微信/QQ/电话/二维码时拒绝提交并记录在这里。</p>' + (d.hits.length ? d.hits.map(function(h){
      return '<div class="cmt" style="display:block"><div class="who">' + E(h.user) + (h.member_no ? '（' + h.member_no + '）' : '') + ' · ' + E(h.place) + ' · <b>' + E(h.kind) + '</b></div><div class="ct" style="color:var(--sub)">' + E(h.snippet) + ' · ' + E(h.time) + '</div></div>';
    }).join('') : '<div class="empty">暂无记录</div>') + '</div>';
  };

  // ---------- 帖子：置顶 / 加精 / 话题 ----------
  window.postFlagButtons = function(p){
    return '<button class="btn sm ' + (p.pinned ? 'gray' : 'ghost') + '" data-pf="' + p.id + '" data-k="pinned" data-v="' + (p.pinned ? '0' : '1') + '">' + (p.pinned ? '取消置顶' : '📌 置顶') + '</button>' +
      '<button class="btn sm ' + (p.featured ? 'gray' : 'ghost') + '" data-pf="' + p.id + '" data-k="featured" data-v="' + (p.featured ? '0' : '1') + '">' + (p.featured ? '取消精选' : '🌟 加精') + '</button>' +
      '<button class="btn sm ' + (p.topic ? 'gray' : 'ghost') + '" data-pf="' + p.id + '" data-k="topic" data-v="' + (p.topic ? '0' : '1') + '">' + (p.topic ? '取消话题' : '🔥 设为话题') + '</button>';
  };
  document.addEventListener('click', async function(ev){
    var b = ev.target.closest && ev.target.closest('[data-pf]'); if (!b) return;
    var body = {}; body[b.dataset.k] = b.dataset.v === '1';
    try {
      var r = await call('POST', '/api/admin/posts/' + b.dataset.pf + '/flags', body);
      msg('已更新 #' + b.dataset.pf + (r.awarded ? '，作者获得 ' + r.awarded + ' 旅行豆' : ''), false);
      var p = {id: b.dataset.pf, pinned: r.pinned, featured: r.featured, topic: r.topic};
      var holder = b.closest('[data-pfh]'); if (holder) holder.innerHTML = postFlagButtons(p);
    } catch(e){}
  });

  // ---------- 服务商入驻申请 ----------
  window.providersAdmin = async function(){
    var box = $('t-providers');
    if (!$('pvList')){
      box.innerHTML = '<div class="card"><h2>服务商入驻申请</h2><p class="tip" style="margin-top:0">申请来自 /partner。联系人、电话、邮箱、KakaoTalk、执照号已加密保存，只在这里显示，请勿外传。通过后显示在 /services；拒绝/下架后不再公开。</p>' +
        '<div class="row" style="gap:8px"><select class="f" id="pvF" style="flex:1"><option value="pending">待审核</option><option value="approved">已通过</option><option value="rejected">已拒绝</option><option value="all">全部</option></select><button class="btn primary" id="pvR">刷新</button></div><div id="pvList" style="margin-top:10px"></div></div>';
      $('pvR').onclick = providersAdmin; $('pvF').onchange = providersAdmin;
    }
    var d = await call('GET', '/api/admin/providers?status=' + $('pvF').value);
    var c = d.counts || {};
    $('pvR').textContent = '刷新（待审 ' + (c.pending || 0) + '）';
    $('pvList').innerHTML = d.items.length ? d.items.map(function(x){
      var ct = x.contact || {};
      return '<div class="cmt" style="display:block"><div class="who">#' + x.id + ' ' + E(x.name) + (x.name_ko ? ' / ' + E(x.name_ko) : '') + ' · <b>' + E(x.status_name) + '</b> · ' + E(x.category_name) + ' · ' + E(x.area) + ' · ' + E(x.time) + '</div>' +
        '<div class="ct" style="white-space:pre-wrap;font-size:13px">' + (x.photo ? '<img src="' + x.photo + '" alt="" style="width:120px;border-radius:8px;float:right;margin-left:8px">' : '') +
        '地址：' + E(x.address || '—') + '\n服务：' + E(x.services) + '\n价格：' + E(x.price) + '\n营业时间：' + E(x.hours) +
        '\n联系人：' + E(ct.name || '') + ' · 电话：' + E(ct.phone || '—') + ' · 邮箱：' + E(ct.email || '—') + ' · Kakao：' + E(ct.kakao || '—') +
        (x.license ? '\n执照/资质：' + E(x.license) : '') + (x.reject_reason ? '\n拒绝原因：' + E(x.reject_reason) : '') + '</div>' +
        '<div class="row" style="margin-top:6px;flex-wrap:wrap;clear:both">' +
        (x.status !== 'approved' ? '<button class="btn sm primary" data-pv="' + x.id + '" data-act="approve">通过并展示</button>' : '') +
        '<button class="btn sm gray" data-pv="' + x.id + '" data-act="reject">' + (x.status === 'approved' ? '下架' : '拒绝') + '</button>' +
        '<button class="btn sm gray" style="color:#c62828" data-pv="' + x.id + '" data-act="delete">删除</button></div></div>';
    }).join('') : '<div class="empty">没有申请</div>';
    document.querySelectorAll('#pvList [data-pv]').forEach(function(b){ b.onclick = async function(){
      var body = {action: b.dataset.act};
      if (b.dataset.act === 'reject'){ var r = prompt('原因（可留空，只在后台记录）', ''); if (r === null) return; body.reason = r; }
      if (b.dataset.act === 'delete' && !confirm('删除申请 #' + b.dataset.pv + '（含加密联系方式），不可恢复？')) return;
      try { await call('POST', '/api/admin/providers/' + b.dataset.pv, body); msg('已处理', false); providersAdmin(); } catch(e){}
    }; });
  };
  // ---------- 开屏广告 ----------
  window.splashAdmin = async function(){
    var d = await call('GET', '/api/admin/splash'), sp = d.splash || {}, box = $('t-splash');
    var fq = Object.keys(d.freqs).map(function(k){ return [k, d.freqs[k]]; });
    box.innerHTML = '<div class="card"><h2>开屏广告 ' + (sp.live ? '<b style="color:#1B7A45;font-size:14px">展示中</b>' : '<b style="color:#999;font-size:14px">未展示</b>') + '</h2>' +
      '<p class="tip" style="margin-top:0">只在首页打开时出现，5 秒后自动关闭，用户可随时跳过。图片建议竖图 9:16（如 1080×1920），底部约 1/7 会被 Logo 白条挡住，重要文字放中上部。跳转只能是站内页面。不要放微信号、二维码或未经核实的优惠。</p>' +
      '<div class="row" style="gap:12px;align-items:flex-start">' + (sp.img ? '<img src="' + sp.img + '" alt="" style="width:110px;border-radius:10px">' : '<div style="width:110px;height:196px;border-radius:10px;background:#f0f1f4;display:flex;align-items:center;justify-content:center;color:#999;font-size:12px">未上传</div>') +
      '<div class="tip" style="flex:1">' + (sp.has_img ? '图片 ' + Math.round((sp.size || 0) / 1024) + 'KB · 更新于 ' + E(sp.updated_at || '') + '<br>' : '') + '展示 ' + (sp.shows || 0) + ' · 点击 ' + (sp.clicks || 0) + ' · 跳过 ' + (sp.skips || 0) + '</div></div>' +
      '<label class="f">更换图片（JPG/PNG/WebP，8MB 内）</label><input type="file" id="spImg" accept="image/jpeg,image/png,image/webp">' +
      '<label class="f">标题（后台备注，选填）</label><input class="f" id="spTitle" maxlength="40" value="' + E(sp.title || '') + '">' +
      '<label class="f">点击跳转（站内路径，如 /deals；留空则点击不跳转）</label><input class="f" id="spLink" value="' + E(sp.link || '') + '" placeholder="/deals">' +
      '<label class="f">展示频率</label><select class="f" id="spFreq">' + opt(fq, sp.freq || 'daily') + '</select>' +
      '<div class="row" style="gap:8px"><div style="flex:1"><label class="f">开始日期</label><input class="f" type="date" id="spS" value="' + E(sp.starts_on || '') + '"></div><div style="flex:1"><label class="f">结束日期</label><input class="f" type="date" id="spE" value="' + E(sp.ends_on || '') + '"></div></div>' +
      '<label style="display:block;font-size:13px;margin-top:8px"><input type="checkbox" id="spLbl"' + (sp.show_label !== false ? ' checked' : '') + '> 左上角显示「广告」标识</label>' +
      '<label style="display:block;font-size:13px;margin-top:6px"><input type="checkbox" id="spOn"' + (sp.enabled ? ' checked' : '') + '> 启用开屏广告</label>' +
      '<button class="btn primary block" id="spGo" style="margin-top:10px">保存</button></div>';
    $('spGo').onclick = async function(){
      var fd = new FormData(), f = $('spImg').files[0];
      if (f) fd.append('image', f, 'splash.jpg');
      fd.append('title', $('spTitle').value); fd.append('link', $('spLink').value.trim()); fd.append('freq', $('spFreq').value);
      fd.append('starts_on', $('spS').value); fd.append('ends_on', $('spE').value);
      fd.append('show_label', $('spLbl').checked ? '1' : ''); fd.append('enabled', $('spOn').checked ? '1' : '');
      this.disabled = true;
      try { await call('POST', '/api/admin/splash', fd); msg('已保存', false); splashAdmin(); } catch(e){}
      this.disabled = false;
    };
  };

  // ---------- 邀请奖励 ----------
  window.referralAdmin = async function(){
    var d = await call('GET', '/api/admin/referral'), c = d.config, s = d.stats, cp = d.coupons, box = $('t-referral');
    var pct = Math.min(100, Math.round(cp.amount / Math.max(1, c.total_cap) * 100));
    box.innerHTML = '<div class="card"><h2>邀请奖励 ' + (c.active ? '<b style="color:#1B7A45;font-size:14px">试运行中</b>' : '<b style="color:#999;font-size:14px">未开放</b>') + '</h2>' +
      '<div class="tip" style="margin-top:0">' + E(c.starts) + ' 至 ' + E(c.ends) + ' · 券有效 60 天 · 单人上限 ¥' + c.person_cap + ' · 只计一级 · 分享动作本身不奖励</div>' +
      '<div style="height:8px;background:#D5E4E1;border-radius:4px;overflow:hidden;margin:10px 0 4px"><i style="display:block;height:100%;width:' + pct + '%;background:#146E68"></i></div>' +
      '<div class="tip">已发券面值 ¥' + cp.amount + ' / 总额度 ¥' + c.total_cap + '（达到后自动停发）</div>' +
      '<div class="row" style="gap:8px;margin-top:10px;flex-wrap:wrap"><button class="btn sm ' + (c.enabled ? 'gray' : 'primary') + '" id="rfTg">' + (c.enabled ? '暂停活动' : '开启活动') + '</button>' +
      '<input class="f" type="date" id="rfEnd" value="' + E(c.ends) + '" style="width:auto;margin:0"><button class="btn sm gray" id="rfEndGo">改结束日期</button></div></div>' +
      '<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap;text-align:center">' +
      [[s.total, '邀请注册'], [s.valid, '有效邀请'], [s.frozen, '冻结待复核'], [s.excluded, '同设备不计入'], [cp.used + ' / ' + cp.issued, '已核销 / 已发券']].map(function(x){ return '<div style="flex:1;min-width:90px;background:#f7f8fa;border-radius:10px;padding:8px"><b style="font-size:18px;display:block">' + x[0] + '</b><span class="tip">' + x[1] + '</span></div>'; }).join('') + '</div></div>' +
      '<div class="card"><h2>待复核 / 有标记</h2>' + (d.review.length ? d.review.map(function(r){
        return '<div class="cmt" style="display:block"><div class="who">#' + r.id + ' ' + E(r.inviter) + ' → ' + E(r.invitee) + ' · <b style="color:' + (r.status === 'frozen' ? '#e65100' : '#999') + '">' + E({frozen: '冻结', excluded: '不计入', pending: '待首次使用', valid: '有效', invalid: '无效'}[r.status] || r.status) + '</b></div>' +
          '<div class="ct" style="color:var(--sub)">' + E(r.reason) + (r.flags ? ' · 标记：' + E(r.flags) : '') + ' · ' + E(r.time) + (r.used ? ' · 已完成首次使用' : '') + '</div>' +
          '<div class="row" style="margin-top:6px;gap:6px"><button class="btn sm gray" data-rf="' + r.id + '" data-a="valid">判为有效</button><button class="btn sm gray" style="color:#c62828" data-rf="' + r.id + '" data-a="invalid">判为无效并收回</button></div></div>';
      }).join('') : '<div class="empty">暂无</div>') + '</div>' +
      '<div class="card"><h2>邀请人（仅后台可见，不对外排名）</h2>' + (d.top.length ? d.top.map(function(t){ return '<div class="cmt"><div class="who">' + E(t.who) + '</div><div class="ct">有效 ' + t.valid + ' / 共 ' + t.total + ' · 已得 ¥' + t.amount + '</div></div>'; }).join('') : '<div class="empty">暂无</div>') +
      '<p class="tip">被邀请人联系方式不在此显示；设备与 IP 只存加盐哈希，用于判断同设备与批量注册。</p></div>';
    $('rfTg').onclick = async function(){ await call('POST', '/api/admin/referral/config', {enabled: !c.enabled}); referralAdmin(); };
    $('rfEndGo').onclick = async function(){ try { await call('POST', '/api/admin/referral/config', {ends: $('rfEnd').value}); msg('已保存', false); referralAdmin(); } catch(e){} };
    box.querySelectorAll('[data-rf]').forEach(function(b){ b.onclick = async function(){
      if (b.dataset.a === 'invalid' && !confirm('判为无效并收回未使用的券？')) return;
      try { await call('POST', '/api/admin/referral/invite/' + b.dataset.rf, {action: b.dataset.a}); msg('已处理', false); referralAdmin(); } catch(e){}
    }; });
  };

  // ---------- 意见反馈 ----------
  window.feedbackAdmin = async function(){
    var d = await call('GET', '/api/admin/feedback'), box = $('t-feedback');
    box.innerHTML = '<div class="card"><h2>意见反馈</h2>' + (d.items.length ? d.items.map(function(f){
      return '<div class="cmt" style="display:block"><div class="who">' + E(f.who) + ' · ' + E(f.time) + (f.status === 'done' ? ' · <span style="color:#1B7A45">已处理</span>' : '') + '</div><div class="ct" style="white-space:pre-wrap">' + E(f.body) + '</div>' +
        (f.status === 'done' ? '' : '<button class="btn sm gray" style="margin-top:6px" data-fb="' + f.id + '">标为已处理</button>') + '</div>';
    }).join('') : '<div class="empty">还没有反馈</div>') + '</div>';
    box.querySelectorAll('[data-fb]').forEach(function(b){ b.onclick = async function(){ await call('POST', '/api/admin/feedback/' + b.dataset.fb, {}); feedbackAdmin(); }; });
  };
})();
