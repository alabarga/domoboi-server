{% load static %}
const CACHE = 'domoboi-v4';

// Only pre-cache same-origin resources — external CDN URLs block SW fetch (no CORS).
// CDN assets (Remixicon, HTMX) are cached lazily on first page load by the fetch handler.
const PRECACHE = [
  '/nilm/',
  '/nilm/locations/',
  '/nilm/offline/',
  '{% static "css/tailwind.css" %}',
  '{% static "location_field/leaflet/leaflet.css" %}',
  '{% static "location_field/leaflet/leaflet.js" %}',
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

  // Only handle http/https — chrome-extension:// etc. cannot be cached
  if (!url.protocol.startsWith('http')) return;

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

// ── Push notifications ────────────────────────────────────────────────────────

self.addEventListener('push', e => {
  if (!e.data) return;
  var data = {};
  try {
    var parsed = e.data.json();
    // pywebpush double-encodes: if result is a string, parse once more
    if (typeof parsed === 'string') parsed = JSON.parse(parsed);
    data = parsed;
  } catch(err) {
    data = {head: 'DOMOBOI', body: e.data.text()};
  }
  e.waitUntil(
    self.registration.showNotification(data.head || 'DOMOBOI', {
      body:    data.body  || '',
      icon:    data.icon  || '/static/images/icon-192.png',
      badge:   '/static/images/icon-192.png',
      data:    {url: data.url || '/nilm/'},
      vibrate: [200, 100, 200],
      tag:     'domoboi-alert',
      renotify: true,
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/nilm/';
  e.waitUntil(
    clients.matchAll({type: 'window', includeUncontrolled: true}).then(cls => {
      for (var i = 0; i < cls.length; i++) {
        if ('focus' in cls[i]) return cls[i].focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
