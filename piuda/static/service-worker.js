const CACHE = "piuda-v18";
const SHELL = [
  "/",
  "/caregiver",
  "/caregiver-manifest.webmanifest",
  "/install",
  "/static/offline.html",
  "/static/app.css?v=18",
  "/static/app.js?v=18",
  "/static/icons/icon-180.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/qr/user.png",
  "/static/qr/caregiver.png"
];

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    const results = await Promise.allSettled(SHELL.map(async url => {
      const response = await fetch(url, { cache: "reload" });
      if (!response.ok) throw new Error(`${url}: ${response.status}`);
      await cache.put(url, response);
    }));
    const failed = results
      .map((result, index) => ({ result, url: SHELL[index] }))
      .filter(item => item.result.status === "rejected")
      .map(item => item.url);
    if (failed.length) console.warn("Piuda shell cache skipped:", failed);
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  const network = fetch(event.request).then(response => ({
    response,
    cacheCopy: response.clone()
  }));
  const backgroundUpdate = network
    .then(async ({ response, cacheCopy }) => {
      if (!response.ok) return;
      const cache = await caches.open(CACHE);
      await cache.put(event.request, cacheCopy);
    })
    .catch(() => {});
  event.waitUntil(backgroundUpdate);

  if (event.request.mode === "navigate") {
    event.respondWith(
      network
        .then(({ response }) => response)
        .catch(async () => (await caches.match(event.request)) || caches.match("/static/offline.html"))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(cached => cached || network.then(({ response }) => response))
  );
});
