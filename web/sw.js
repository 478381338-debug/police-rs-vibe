/* 青山卫士移动端 Service Worker：只缓存应用外壳，不缓存 API 和上传请求。 */
const CACHE = "qs-mobile-shell-v1";
const SHELL = [
  "/m",
  "/static/mobile.css",
  "/static/mobile.js",
  "/mobile.webmanifest",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  /* API 一律走网络：案件状态和证据完整性不能拿旧缓存冒充。 */
  if (url.pathname.startsWith("/api/")) return;

  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(async () => {
        const hit = await caches.match(req);
        if (hit) return hit;
        if (req.mode === "navigate") {
          const shell = await caches.match("/m");
          if (shell) return shell;
        }
        return new Response("离线且无缓存", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
      })
  );
});
