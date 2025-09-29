const CACHE_NAME = "moneyapp-cache-v1";
const urlsToCache = [
  "/",
  "/static/style.css",
  "/static/icons/icon-192x192.png",
  "/static/icons/icon-512x512.png"
];

// Install Service Worker
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(urlsToCache);
    })
  );
});

// Fetch (Offline Support)
self.addEventListener("fetch", event => {
  event.respondWith(
    caches.match(event.request).then(response => {
      return response || fetch(event.request);
    })
  );
});
