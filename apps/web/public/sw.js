/*
 * Fourth Down Edge service worker.
 *
 * Strategy:
 *  - Precache the application shell AND the hashed build assets on install.
 *    The asset list and cache version are injected at build time by
 *    scripts/build-sw.mjs; relying on runtime caching alone is unreliable
 *    because browsers may serve hashed assets from the memory cache without
 *    ever dispatching a fetch event.
 *  - Navigation requests: network-first, falling back to the cached shell so
 *    the app opens offline.
 *  - Static assets: cache-first (they are content-hashed and immutable).
 *  - API/data requests are NOT cached here. Market data must never be served
 *    from cache as if current; the app computes staleness from embedded data
 *    timestamps and renders explicit STALE banners. When offline, the app
 *    shell loads and the UI shows its disconnected/stale states.
 *
 * All cache reads pass { ignoreVary: true }. Static hosts commonly send
 * `Vary: Origin`, and module-script imports carry an `Origin` header that
 * plain navigations and fetch() do not — without ignoreVary those lookups
 * miss the cache and the app fails to boot offline.
 */
const MATCH_OPTS = { ignoreVary: true };

const CACHE_VERSION = '__FDE_CACHE_VERSION__';
const PRECACHE_URLS = __FDE_PRECACHE_URLS__;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_VERSION)
      // addAll is atomic: if any asset fails the install fails, so we never
      // activate a half-populated cache that would break offline loads.
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  let url;
  try {
    url = new URL(req.url);
  } catch {
    return;
  }
  // Never touch cross-origin traffic (e.g. Supabase auth).
  if (url.origin !== self.location.origin) return;

  // App-shell navigation: network first, cached shell as the offline fallback.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE_VERSION).then((c) => c.put('/index.html', copy));
          }
          return res;
        })
        .catch(async () => {
          const cached = await caches.match('/index.html', MATCH_OPTS);
          return (
            cached ??
            new Response('<!doctype html><title>Offline</title><p>Fourth Down Edge is offline.</p>', {
              status: 503,
              headers: { 'Content-Type': 'text/html' },
            })
          );
        }),
    );
    return;
  }

  // Static assets: cache-first (content-hashed filenames are immutable).
  if (/\.(js|css|png|svg|woff2?)$/.test(url.pathname) || url.pathname === '/manifest.webmanifest') {
    event.respondWith(
      caches.match(req, MATCH_OPTS).then((cached) => {
        if (cached) return cached;
        return fetch(req).then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE_VERSION).then((c) => c.put(req, copy));
          }
          return res;
        });
      }),
    );
  }
});
