// UX-16: 오프라인 셸용 서비스워커. 앱 셸(app shell) 정적 자원만 캐시하고, API
// 요청(/v1/...)은 항상 네트워크로 보낸다 — 봉투 응답을 오프라인 중 캐시된
// 옛 데이터로 착각해 보여주면 안 되므로(UX-A5류 원칙과 동형) 여기서 다루지 않는다.
const CACHE_VERSION = "aios-shell-v1";
const APP_SHELL = ["/", "/index.html", "/manifest.webmanifest", "/offline.html", "/favicon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_VERSION)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.pathname.startsWith("/v1/")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(
        () => caches.match("/offline.html").then((res) => res || caches.match("/index.html")),
      ),
    );
    return;
  }

  event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
});
