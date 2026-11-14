/* 旅韩助手：注册 Service Worker，并提供可关闭的安装提示 */
(function(){
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
