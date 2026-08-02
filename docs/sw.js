const V = 'b103e0b25ac7';
// 폰트와 차트 라이브러리도 미리 받아 둔다. 이게 없으면 오프라인에서 글자가
// 시스템 폰트로 바뀌고 차트가 아예 안 그려진다.
const SHELL = ['./', './index.html', './signal.html',
               './icon-192.png', './icon-512.png',
               './apple-touch-icon.png', './manifest.webmanifest',
               './assets/vendor/lightweight-charts.standalone.production.js',
               './assets/fonts/noto-serif-kr-korean-400.woff2',
               './assets/fonts/noto-serif-kr-latin-400.woff2',
               './assets/fonts/pretendard-400.woff2',
               './assets/fonts/pretendard-600.woff2',
               './assets/fonts/ibm-plex-mono-400.woff2',
               './assets/fonts/ibm-plex-mono-500.woff2'];

self.addEventListener('install', e => {
  // addAll 은 하나라도 실패하면 전부 취소된다 — 경로 오타 하나에 오프라인이
  // 통째로 죽는다는 뜻이다. 하나씩 담고 실패는 넘긴다.
  e.waitUntil(caches.open(V)
    .then(c => Promise.all(SHELL.map(u => c.add(u).catch(() => {}))))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const path = new URL(req.url).pathname;
  // 지수 요약도 페이지와 같이 다룬다. 캐시 우선으로 두면 어제 숫자를 오늘 값처럼
  // 보여주고, 캐시를 아예 안 하면 오프라인에서 시장 화면이 통째로 빈다.
  const isPage = req.mode === 'navigate' || path.endsWith('/') ||
                 path.endsWith('index.html') || path.endsWith('signal.html') ||
                 path.endsWith('market_signal.json');
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
