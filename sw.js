/* Offline support. The page is fetched from the network first, so timetable updates arrive
   as soon as they're published; the last copy is kept for when there's no connection.
   Leaflet and the icons are cached at install. Map tiles you have looked at are kept too (up to
   TILE_MAX, oldest dropped first), so places you've seen load instantly and still show offline. */
const CACHE = 'burgosbus-v1';
const TILES = 'burgosbus-tiles';
const TILE_MAX = 1500;   /* roughly 30 MB of retina tiles */
const PAGE = './';
const STATIC = ['https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js',
                './apple-touch-icon.png', './icons/icon-192.png', './icons/icon-512.png', './manifest.webmanifest'];

/* Cache Leaflet and the icons at install too. The page that registers the worker has already
   loaded them without it, so otherwise they'd only be cached on the next visit, and a home-screen
   app opened once and then used offline would have no map. One failure mustn't stop the install. */
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE)
    .then(c => c.add(PAGE).then(() => Promise.all(STATIC.map(s => c.add(s).catch(() => {})))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE && k !== TILES).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  /* The page itself (any ?v= or ?check= variant): network first, cached copy offline. */
  if (req.mode === 'navigate' || (url.origin === location.origin && url.pathname === new URL(PAGE, location).pathname)) {
    e.respondWith(fetch(req).then(res => {
      if (res.ok && req.mode === 'navigate') {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(PAGE, copy));
      }
      return res;
    }).catch(() => caches.match(PAGE)));
    return;
  }

  /* Map tiles: cache first. */
  if (url.hostname === 'basemaps.cartocdn.com') { e.respondWith(tile(req, url)); return; }

  /* Leaflet and icons: cache first. */
  if (STATIC.some(s => new URL(s, location).href === url.href)) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
      return res;
    })));
  }
});

/* Tiles are fetched in CORS mode so they can be stored without the size padding browsers add to
   opaque responses. If the tile server ever refuses CORS, fall back to a plain fetch (uncached). */
let puts = 0, noCors = false;
async function tile(req, url) {
  const c = await caches.open(TILES);
  const hit = await c.match(url.href);
  if (hit) return hit;
  if (noCors) return fetch(req);
  let res;
  try { res = await fetch(url.href, { mode: 'cors', credentials: 'omit' }); }
  catch (err) {
    const plain = await fetch(req);   /* offline: this throws too, and the tile just stays blank */
    noCors = true;                    /* online but refused: stop trying CORS for this session */
    return plain;
  }
  if (res.ok) {
    c.put(url.href, res.clone()).then(() => { if (++puts % 50 === 0) trim(c); }).catch(() => {});
  }
  return res;
}
async function trim(c) {
  const keys = await c.keys();
  if (keys.length > TILE_MAX) await Promise.all(keys.slice(0, keys.length - TILE_MAX).map(k => c.delete(k)));
}
