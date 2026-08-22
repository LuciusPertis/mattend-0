/* mattend service worker -- version 4.3.12
 *
 * The version literal below is rewritten by tools/bump_version.py and must match
 * docs/version.js (there is a test for that). Two things depend on it changing:
 *
 *   1. CACHE_NAME, so a new version cannot serve the previous one's files;
 *   2. this file's own bytes, which is what makes the browser notice there is a
 *      new worker at all.
 *
 * Fetching is network-first, not cache-first. The old cache-first version meant
 * a phone that had once loaded the app would keep running that code forever --
 * new protocol, new fields, invisible. Now the cache is only a fallback for
 * being offline, and a stale phone heals itself the moment it has signal.
 */
const VERSION = "4.3.12";
const CACHE_NAME = `mattend-${VERSION}`;
const SHELL = ["./", "./index.html", "./manifest.json",
               "./version.js", "./protocol.js", "./enroll.js"];

self.addEventListener("install", (event) => {
    // Take over immediately rather than waiting for every tab to close.
    self.skipWaiting();
    event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)));
});

self.addEventListener("activate", (event) => {
    event.waitUntil((async () => {
        const names = await caches.keys();
        await Promise.all(names.map((name) => name === CACHE_NAME ? null : caches.delete(name)));
        await self.clients.claim();
    })());
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET" || !request.url.startsWith(self.location.origin)) return;

    event.respondWith((async () => {
        try {
            const fresh = await fetch(request);
            if (fresh && fresh.ok) {
                const cache = await caches.open(CACHE_NAME);
                cache.put(request, fresh.clone());
            }
            return fresh;
        } catch (err) {
            const cached = await caches.match(request);
            if (cached) return cached;
            throw err;
        }
    })());
});

self.addEventListener("message", (event) => {
    if (event.data === "version") event.source.postMessage({ version: VERSION });
});
