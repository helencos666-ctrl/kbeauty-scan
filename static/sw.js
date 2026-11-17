/* 旅韩助手 service worker: cache app shell, never cache API or POST. */
const CACHE = 'lvhan-shell-v53';
const SHELL = [
  '/',
  '/attractions',
  '/food',
  '/shopping',
  '/tips',
  '/faq',
  '/beauty',
  '/community',
  '/member',
  '/login',
  '/rules',
  '/community/boards',
  '/community/friends',
  '/community/friends/verify',
  '/community/rate',
  '/privacy',
  '/terms',
  '/static/index.html',
  '/static/attractions.html',
  '/static/attractions.json',
  '/static/food.html',
  '/static/food.json',
  '/static/shopping.html',
  '/static/shopping-stores.json',
  '/static/tips.html',
  '/static/faq.html',
  '/static/beauty.html',
  '/static/guide.css',
  '/static/maps.js',
  '/static/phrases.js',
  '/static/subway.js',
  '/static/subway.json',
  '/static/stations.json',
  '/static/community.css?v=53',
  '/static/chat.css?v=53',
  '/static/community.js?v=53',
  '/static/community.html',
  '/static/community-post.html',
  '/static/community-new.html',
  '/static/login.html',
  '/static/member.html',
  '/static/rules.html',
  '/static/community-boards.html',
  '/static/friends.html',
  '/static/friend.html',
  '/static/friend-edit.html',
  '/static/buddy-verify.html',
  '/static/messages.html',
  '/static/chat.html',
  '/static/rate.html',
  '/static/privacy.html',
  '/static/terms.html',
  '/static/pwa.js',
  '/static/banners.js',
  '/static/pwa.css',
  '/manifest.webmanifest',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-192-maskable.png',
  '/static/icons/icon-512-maskable.png',
  '/static/icons/apple-touch-icon.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

function isApi(url) {
  return url.pathname === '/api' || url.pathname.startsWith('/api/');
}

function isHtml(request, url) {
  if (request.mode === 'navigate') return true;
  const accept = request.headers.get('accept') || '';
  if (accept.includes('text/html')) return true;
  return url.pathname === '/' || url.pathname === '/attractions' ||
    url.pathname.endsWith('.html');
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  const cachedP = cache.match(request, { ignoreSearch: true });
  const freshP = fetch(request).then(fresh => {
    if (fresh && fresh.ok && fresh.type !== 'opaque') {
      cache.put(request, fresh.clone());
    }
    return fresh;
  });
  // iOS 返回时若只等网络，页面会长时间白屏；1.5 秒内没回来就先用缓存壳，再靠页面恢复路线
  const timed = new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 1500));
  try {
    return await Promise.race([freshP, timed]);
  } catch (err) {
    const cached = await cachedP;
    if (cached) return cached;
    try {
      return await freshP;
    } catch (err2) {
      if (request.mode === 'navigate') {
        const fallback = await cache.match('/') || await cache.match('/static/index.html');
        if (fallback) return fallback;
      }
      throw err2;
    }
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request, { ignoreSearch: true });
  if (cached) {
    fetch(request).then(fresh => {
      if (fresh && fresh.ok && fresh.type !== 'opaque') cache.put(request, fresh.clone());
    }).catch(() => {});
    return cached;
  }
  const fresh = await fetch(request);
  if (fresh && fresh.ok && fresh.type !== 'opaque') cache.put(request, fresh.clone());
  return fresh;
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return; // POST and others always go to network (browser default)
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isApi(url)) return; // never intercept /api/*
  if (url.pathname === '/admin' || url.searchParams.has('ref')) return; // admin and referral visits always hit the network

  if (isHtml(request, url)) {
    event.respondWith(networkFirst(request));
    return;
  }
  // App-shell assets (JSON, icons, JS, CSS, manifest)
  const shellExt = /\.(json|js|css|png|webmanifest|mp3)$/i.test(url.pathname) ||
    url.pathname === '/manifest.webmanifest';
  if (shellExt) {
    event.respondWith(cacheFirst(request));
  }
});

// 站内咨询提醒：点通知打开咨询列表
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/consult';
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) { if ('focus' in c) { c.navigate(url).catch(() => {}); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
