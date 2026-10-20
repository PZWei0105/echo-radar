// 回波的離線外殼：介面先用快取打開，資料一律先問網路（沒網路才用上次的）。
const V = 'echo-__BUILD__';
const SHELL = ['./', 'index.html', 'styles.css?v=__BUILD__', 'app.js?v=__BUILD__', 'radar.js?v=__BUILD__', 'brief.js?v=__BUILD__',
  'manifest.webmanifest',
  'icons/icon-192.png', 'icons/apple-touch-icon.png'];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(V).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  const isData = url.pathname.includes('/data/');
  const isPage = e.request.mode === 'navigate';
  if (isData || isPage) {
    e.respondWith(fetch(e.request).then((r) => {
      const cp = r.clone();
      caches.open(V).then((c) => c.put(isPage ? 'index.html' : e.request, cp));
      return r;
    }).catch(() => caches.match(isPage ? 'index.html' : e.request)));
    return;
  }
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((r) => {
    const cp = r.clone();
    caches.open(V).then((c) => c.put(e.request, cp));
    return r;
  })));
});
