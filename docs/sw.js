const V = '1af9785a931b';
const SHELL = ['./', './index.html', './icon-192.png', './icon-512.png',
               './apple-touch-icon.png', './manifest.webmanifest'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const isPage = req.mode === 'navigate' || new URL(req.url).pathname.endsWith('/') ||
                 new URL(req.url).pathname.endsWith('index.html');
  if (isPage) {
    // 숫자는 늘 최신이어야 한다. 네트워크가 죽었을 때만 캐시로 떨어진다.
    e.respondWith(fetch(req)
      .then(r => { const copy = r.clone();
                   caches.open(V).then(c => c.put(req, copy)); return r; })
      .catch(() => caches.match(req).then(r => r || caches.match('./index.html'))));
  } else {
    e.respondWith(caches.match(req).then(r => r || fetch(req)));
  }
});
