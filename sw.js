/* Field Kit suite — offline service worker.
   HTML is network-first (always loads the latest when online, falls back to cache offline),
   static assets are cache-first. Bump CACHE on any change so clients refresh. */
const CACHE = 'fieldkit-v14';
const ASSETS = [
  './',
  './index.html', './tickers.html', './scrub.html', './markets.html', './tax.html', './places.html',
  './leaflet.js', './leaflet.css',
  './manifest-field.webmanifest', './manifest-watch.webmanifest', './manifest-scrub.webmanifest', './manifest-markets.webmanifest', './manifest-tax.webmanifest', './manifest-places.webmanifest',
  './field-192.png', './field-512.png', './field-180.png',
  './watch-192.png', './watch-512.png', './watch-180.png',
  './scrub-192.png', './scrub-512.png', './scrub-180.png',
  './tax-192.png', './tax-512.png', './tax-180.png',
  './places-192.png', './places-512.png', './places-180.png'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.allSettled(ASSETS.map(a => c.add(a))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;  // APIs / relays / radar → straight to network

  const isHTML = req.mode === 'navigation'
    || url.pathname.endsWith('.html')
    || url.pathname.endsWith('/');

  if (isHTML) {
    // network-first: newest version wins; cache is only the offline safety net
    e.respondWith(
      fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy));
        return res;
      }).catch(() =>
        caches.match(req).then(hit => hit || caches.match('./index.html'))
      )
    );
  } else {
    // static assets (icons, manifests): cache-first, refresh in the background
    e.respondWith(
      caches.match(req).then(hit =>
        hit || fetch(req).then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy));
          return res;
        })
      )
    );
  }
});
