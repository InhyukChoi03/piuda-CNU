const CACHE = "piuda-v22";
const SHELL = [
  "/",
  "/caregiver-manifest.webmanifest",
  "/install",
  "/static/offline.html",
  "/static/app.css?v=22",
  "/static/app.js?v=22",
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

  // 보호자 화면은 최신 알림 데이터가 필요한 온라인 전용 화면입니다.
  // 네트워크 문제가 있을 때 오래된 로그인 HTML을 보여 주면
  // 실제 원인이 가려지므로 navigation 응답을 캐시하지 않습니다.
  if (event.request.mode === "navigate" && url.pathname === "/caregiver") {
    event.respondWith(
      fetch(event.request, { cache: "no-store" })
        .catch(() => caches.match("/static/offline.html"))
    );
    return;
  }

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
