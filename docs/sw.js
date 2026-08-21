const CACHE_NAME = 'student-node-v2';
self.addEventListener('install', (e) => {
    e.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(['./', './index.html', './manifest.json', './protocol.js'])));
});
self.addEventListener('fetch', (e) => {
    e.respondWith(caches.match(e.request).then((response) => response || fetch(e.request)));
});