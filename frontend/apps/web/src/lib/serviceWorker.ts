// UX-16: 서비스워커 등록. React StrictMode(task-481 main.tsx)는 effect를 2번
// 실행하고, OfflineBanner/InstallPrompt 등 여러 컴포넌트가 각자 마운트 시점에
// 등록을 트리거할 수 있어 register() 호출이 중복될 수 있다 — 진행 중인 등록을
// 재사용(memoize)해 실제 navigator.serviceWorker.register 호출은 항상 1회만
// 나가게 한다.
export interface ServiceWorkerRegistrationHandle {
  registration: ServiceWorkerRegistration;
  updateAvailable: () => boolean;
}

let pending: Promise<ServiceWorkerRegistrationHandle | null> | null = null;

/** 테스트 전용: 메모이즈된 등록 프라미스를 초기화한다. */
export function resetServiceWorkerRegistration(): void {
  pending = null;
}

export function isServiceWorkerSupported(): boolean {
  return typeof navigator !== "undefined" && "serviceWorker" in navigator;
}

export async function registerServiceWorker(
  swUrl = "/sw.js",
  onUpdateAvailable?: () => void,
): Promise<ServiceWorkerRegistrationHandle | null> {
  if (!isServiceWorkerSupported()) {
    return null;
  }
  if (pending) {
    return pending;
  }

  pending = (async () => {
    try {
      const registration = await navigator.serviceWorker.register(swUrl);
      // 최초 설치(컨트롤러 없음)에서는 "업데이트 있음" 배너를 띄우지 않는다 —
      // 첫 방문자에게는 갱신 알림이 아니라 그냥 정상 설치일 뿐이다.
      const hadController = Boolean(navigator.serviceWorker.controller);
      let updateAvailable = false;

      registration.addEventListener("updatefound", () => {
        const installing = registration.installing;
        if (!installing) return;
        installing.addEventListener("statechange", () => {
          if (installing.state === "installed" && hadController) {
            updateAvailable = true;
            onUpdateAvailable?.();
          }
        });
      });

      return { registration, updateAvailable: () => updateAvailable };
    } catch {
      // 등록 실패는 앱을 깨뜨리지 않는다(오프라인 셸은 향상 기능) — 다음 호출이
      // 재시도할 수 있도록 memo를 비운다.
      pending = null;
      return null;
    }
  })();

  return pending;
}
