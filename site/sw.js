// 回波的離線外殼：介面先用快取打開，資料一律先問網路（沒網路才用上次的）。
const V = 'echo-__BUILD__';
const SHELL = ['./', 'index.html', 'styles.css?v=__BUILD__', 'app.js?v=__BUILD__', 'radar.js?v=__BUILD__', 'brief.js?v=__BUILD__', 'updates.js?v=__BUILD__',
  'manifest.webmanifest',
  'icons/icon-192.png', 'icons/apple-touch-icon.png'];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(V).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
// 資料檔的快取鑰匙不含 ?t= 這類防快取參數，每個檔案只留一份
function dataKey(url) {
  const u = new URL(url);
  u.search = '';
  return u.toString();
}
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  const isData = url.pathname.includes('/data/');
  const isPage = e.request.mode === 'navigate';
  if (isData || isPage) {
    const key = isPage ? 'index.html' : dataKey(e.request.url);
    e.respondWith(fetch(e.request).then((r) => {
      // 只存成功的回應：GitHub Pages 發布中短暫的 404 不能蓋掉離線備份
      if (r.ok) {
        const cp = r.clone();
        caches.open(V).then((c) => c.put(key, cp));
      }
      return r;
    }).catch(() => caches.match(key)));
    return;
  }
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((r) => {
    if (r.ok) {
      const cp = r.clone();
      caches.open(V).then((c) => c.put(e.request, cp));
    }
    return r;
  })));
});
