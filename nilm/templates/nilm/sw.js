{% load static %}
const CACHE = 'domoboi-v1';

const PRECACHE = [
  '/nilm/',
  '/nilm/locations/',
  '/nilm/offline/',
  '{% static "location_field/leaflet/leaflet.css" %}',
  '{% static "location_field/leaflet/leaflet.js" %}',
  'https://cdn.jsdelivr.net/npm/remixicon@3.5.0/fonts/remixicon.css',
  'https://cdn.tailwindcss.com',
  'https://unpkg.com/htmx.org@2.0.3',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
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
  const url = new URL(req.url);

  // Never cache POST / non-GET
  if (req.method !== 'GET') return;

  // Never cache HTMX partials (HX-Request header means dynamic data)
  if (req.headers.get('HX-Request')) return;

  // Never cache i18n language switch or admin
  if (url.pathname.startsWith('/i18n/') || url.pathname.startsWith('/admin/')) return;

  // Cache-first for static assets (CDN + local)
  if (
    url.hostname !== location.hostname ||
    url.pathname.startsWith('/static/')
  ) {
    e.respondWith(
      caches.match(req).then(cached => cached || fetch(req).then(res => {
        if (res && res.status === 200) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(req, clone));
        }
        return res;
      }))
    );
    return;
  }

  // Network-first for app HTML pages, fallback to cache, then offline
  e.respondWith(
    fetch(req)
      .then(res => {
        if (res && res.status === 200) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(req, clone));
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then(cached => cached || caches.match('/nilm/offline/'))
      )
  );
});
