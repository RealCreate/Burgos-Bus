/* Offline support. The page is fetched from the network first, so timetable updates arrive
   as soon as they're published; the last copy is kept for when there's no connection.
   Leaflet, the map code and the icons are cached at install. The street map is one file
   (map/burgos.pmtiles) that the page reads in small pieces; every piece you've needed is kept
   (up to PIECE_MAX, oldest dropped first), so places you've seen load instantly and still show
   offline, without ever downloading the whole file. */
const CACHE = 'burgosbus-v3';
const TILES = 'burgosbus-map';
const PIECE_MAX = 3000;   /* pieces are small (a few KB each), so this is roughly 15 MB at most */
const PAGE = './';
const STATIC = ['./lib/leaflet-1.9.4.js', './lib/protomaps-leaflet-5.1.0.js',
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

  /* Pieces of the street map: cache first. */
  if (url.origin === location.origin && url.pathname.endsWith('/map/burgos.pmtiles') && req.headers.get('range')) {
    e.respondWith(piece(req, url)); return;
  }

  /* Leaflet and icons: cache first. */
  if (STATIC.some(s => new URL(s, location).href === url.href)) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
      return res;
    })));
  }
});

/* Each piece is stored under the file's address plus its byte range. The Cache API refuses partial
   (206) responses, so a piece is stored as a plain response and turned back into a 206 when served.
   The file's address carries its build date (?v=), so pieces of an old map are never mixed with a new
   one; they're simply never asked for again and age out. If anything goes wrong, the network answers. */
let puts = 0;
async function piece(req, url) {
  const range = req.headers.get('range');
  const key = url.href + (url.search ? '&' : '?') + 'range=' + encodeURIComponent(range);
  const c = await caches.open(TILES);
  const hit = await c.match(key);
  if (hit) {
    const h = new Headers(hit.headers);
    return new Response(hit.body, { status: 206, statusText: 'Partial Content', headers: h });
  }
  const res = await fetch(req);
  if (res.status === 206) {
    const body = await res.clone().arrayBuffer();
    const h = new Headers();
    for (const k of ['content-type', 'content-range', 'etag', 'last-modified']) { const v = res.headers.get(k); if (v) h.set(k, v); }
    h.set('content-length', String(body.byteLength));
    c.put(key, new Response(body, { status: 200, headers: h }))
      .then(() => { if (++puts % 100 === 0) trim(c); }).catch(() => {});
  }
  return res;
}
async function trim(c) {
  const keys = await c.keys();
  if (keys.length > PIECE_MAX) await Promise.all(keys.slice(0, keys.length - PIECE_MAX).map(k => c.delete(k)));
}
