// 캐시를 둘로 나눈다.
//   ASSETS — 폰트·차트 라이브러리·아이콘. 내용이 바뀌지 않는다.
//   PAGES  — html 과 지수 json. 데이터가 바뀌면 같이 바뀐다.
// 하나로 두면 캐시 이름이 페이지 해시라 데이터가 바뀌는 날마다 캐시가 통째로
// 갈리고, 폰트 700KB 를 매일 다시 받게 된다.
const V = '93b4bb30a160';
const PAGES = 'pages-' + V;
const ASSETS = 'assets-282656e0';
const STATIC = ['./assets/vendor/lightweight-charts.standalone.production.js',
                './assets/fonts/noto-serif-kr-korean-400.woff2',
                './assets/fonts/noto-serif-kr-latin-400.woff2',
                './assets/fonts/pretendard-400.woff2',
                './assets/fonts/pretendard-600.woff2',
                './assets/fonts/ibm-plex-mono-400.woff2',
                './assets/fonts/ibm-plex-mono-500.woff2',
                './icon-192.png', './icon-512.png', './apple-touch-icon.png',
                './manifest.webmanifest'];
// 첫 화면이 읽는 데이터 파일들. 숫자라서 페이지와 같이 다뤄야 한다 —
// 캐시 우선으로 두면 어제 값을 오늘처럼 보여주고, 아예 안 담으면 오프라인에서
// 화면이 통째로 빈다.
const DATA_FILES = ['market_signal.json', 'screen.json', 'market_tree.json',
                    'market_calendar.json', 'market_macro.json',
                    'market_etf.json', 'market_news.json',
                    'market_theme.json', 'market_strong.json',
                    'market_world.json', 'market_stocknews.json',
                    'market_board.json', 'market_headline.json'];
// 종목 일봉(1MB)은 목록만 보는 사람에게는 필요 없다. 미리 받지 않되, 한 번
// 받으면 캐시에 남겨 다음에 같은 종목을 열 때 다시 받지 않게 한다.
const LAZY_FILES = ['market_px.json'];
const SHELL = ['./', './index.html', './stocks.html']
                .concat(DATA_FILES.map(f => './' + f));

self.addEventListener('install', e => {
  // addAll 은 하나라도 실패하면 전부 취소된다 — 경로 오타 하나에 오프라인이
  // 통째로 죽는다는 뜻이다. 하나씩 담고 실패는 넘긴다.
  const put = (name, urls) => caches.open(name)
    .then(c => Promise.all(urls.map(u => c.add(u).catch(() => {}))));
  e.waitUntil(Promise.all([put(ASSETS, STATIC), put(PAGES, SHELL)])
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks
      .filter(k => k !== PAGES && k !== ASSETS)
      .map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // 바깥 주소는 건드리지 않는다. 현황판이 live 가지(raw.githubusercontent)를
  // 볼 때 파일 이름이 우리 것과 같아서, 걸러내지 않으면 그 응답까지 우리
  // 캐시에 들어가고 실패했을 때 index.html 을 JSON 이라고 돌려주게 된다.
  if (url.origin !== self.location.origin) return;
  const path = url.pathname;
  // 지수 요약도 페이지와 같이 다룬다. 캐시 우선으로 두면 어제 숫자를 오늘 값처럼
  // 보여주고, 캐시를 아예 안 하면 오프라인에서 시장 화면이 통째로 빈다.
  const isPage = req.mode === 'navigate' || path.endsWith('/') ||
                 path.endsWith('index.html') || path.endsWith('stocks.html') ||
                 DATA_FILES.some(f => path.endsWith(f)) ||
                 LAZY_FILES.some(f => path.endsWith(f));
  if (isPage) {
    e.respondWith(fetch(req)
      .then(r => { const copy = r.clone();
                   caches.open(PAGES).then(c => c.put(req, copy)); return r; })
      .catch(() => caches.match(req).then(r => r || caches.match('./index.html'))));
  } else {
    e.respondWith(caches.match(req).then(r => r || fetch(req)));
  }
});
