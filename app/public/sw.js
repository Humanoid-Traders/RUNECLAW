// Minimal service worker: exists only to satisfy PWA installability
// requirements. It deliberately caches nothing -- a caching SW risks
// serving stale JS/CSS after a deploy, and this dashboard already relies on
// polling/SSE for freshness, so every request is left to hit the network
// exactly as it would with no service worker at all.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {}); // no-op passthrough
