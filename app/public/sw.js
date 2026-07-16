// Minimal service worker: exists only to satisfy PWA installability
// requirements. It deliberately caches nothing -- a caching SW risks
// serving stale JS/CSS after a deploy, and this dashboard already relies on
// polling/SSE for freshness, so every request is left to hit the network
// exactly as it would with no service worker at all.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {}); // no-op passthrough

// Web push: the server sends { title, body, url } (opt-in subscriptions only;
// payloads are the bot's already-sanitized public feed events).
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { /* text */ }
  const title = data.title || 'RUNECLAW';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/app_icon_256.png',
    badge: '/app_icon_256.png',
    data: { url: data.url || '/dashboard#feed' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/dashboard#feed';
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then((wins) => {
      for (const w of wins) {
        if (w.url.includes('/dashboard') && 'focus' in w) { w.navigate(url); return w.focus(); }
      }
      return self.clients.openWindow(url);
    }));
});
