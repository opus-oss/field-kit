/* Field Kit suite — offline service worker.
   Bump CACHE when you change any app file so phones pick up the new version. */
const CACHE = 'fieldkit-v1';
const ASSETS = [
  './',
  './index.html', './tickers.html', './scrub.html',
  './manifest-field.webmanifest', './manifest-watch.webmanifest', './manifest-scrub.webmanifest',
  './field-192.png', './field-512.png', './field-180.png',
  './watch-192.png', './watch-512.png', './watch-180.png',
  './scrub-192.png', './scrub-512.png', './scrub-180.png'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c =>
      // cache each individually so one missing file can't abort the whole install
      Promise.allSettled(ASSETS.map(a => c.add(a)))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // App shell (same origin): cache-first, then network, and keep the cache fresh.
  if (url.origin === location.origin) {
    e.respondWith(
      caches.match(req).then(hit =>
        hit || fetch(req).then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy));
          return res;
        }).catch(() =>
          // offline & uncached navigation → fall back to the Field Kit shell
          req.mode === 'navigation' ? caches.match('./index.html') : undefined
        )
      )
    );
    return;
  }
  // Everything cross-origin (weather / market / geocoding APIs, radar) → straight to network.
  // These are live data; if offline they fail and the apps show their own graceful message.
});
