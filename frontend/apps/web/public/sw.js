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
  // 오프라인 셸은 앱 자체 오리진의 정적 자원만 대상으로 한다 — API 백엔드는
  // 별도 오리진(VITE_API_BASE_URL, clientInstance.ts)이라 origin 검사 없이
  // pathname만 보면 교차 오리진 API GET(예: /executions, /users/me — /v1/
  // 접두사가 없는 레거시 라우트)까지 SW가 가로채 자신의 워커 컨텍스트에서
  // 재요청하게 된다. 그 재요청은 페이지 쪽 네트워크 계층(Playwright
  // page.route 포함)을 우회해 별도 실패 경로가 된다.
  if (url.origin !== self.location.origin) return;
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

// UX-17: 푸시 수신. navigator.serviceWorker.register("/sw.js")는 스코프("/")가
// 겹치면 마지막 등록만 활성 워커로 남으므로(둘 이상의 스크립트를 같은 스코프에
// 등록하면 나중 것이 이전 것을 교체한다), 오프라인 셸(위 install/activate/fetch)과
// 푸시 리스너를 별도 스크립트가 아니라 이 파일 하나에 함께 둔다 — 별도 push-sw.js를
// 두면 그것이 활성화되는 순간 앱 셸 캐싱(fetch 핸들러)이 사라져 UX-16 오프라인
// 진입이 깨진다.
self.addEventListener("push", (event) => {
  let payload = { title: "AIOS", body: "" };
  if (event.data) {
    try {
      payload = event.data.json();
    } catch {
      payload = { title: "AIOS", body: event.data.text() };
    }
  }

  event.waitUntil(
    self.registration.showNotification(payload.title ?? "AIOS", {
      body: payload.body ?? "",
      icon: "/icons.svg",
      data: { url: payload.url ?? "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url ?? "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url === targetUrl && "focus" in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
    }),
  );
});
