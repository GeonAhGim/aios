// UX-17 DoD(구독·해지·수신)의 "수신" 절반. 서비스워커 스코프는 vitest/jsdom이
// 실행하지 않는 순수 브라우저 런타임이라 단위 테스트 대상이 아니다(구독/해지
// 로직의 negative·실패주입·성능 단언은 push.ts/push.test.ts, usePushNotifications.ts/
// usePushNotifications.test.ts가 담당) — usePushNotifications.ts가
// navigator.serviceWorker.register("/push-sw.js")로 등록한다.
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
