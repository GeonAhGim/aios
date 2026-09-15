import { afterEach, describe, expect, it, vi } from "vitest";
import { perfBudgetMs } from "../test/perfBudget";
import {
  isServiceWorkerSupported,
  registerServiceWorker,
  resetServiceWorkerRegistration,
} from "./serviceWorker";

class FakeWorker extends EventTarget {
  state: string;
  constructor(state = "installing") {
    super();
    this.state = state;
  }
  transitionTo(state: string) {
    this.state = state;
    this.dispatchEvent(new Event("statechange"));
  }
}

class FakeRegistration extends EventTarget {
  installing: FakeWorker | null = null;
  triggerUpdateFound(worker: FakeWorker) {
    this.installing = worker;
    this.dispatchEvent(new Event("updatefound"));
  }
}

function installFakeServiceWorkerContainer(opts: {
  register: (url: string) => Promise<FakeRegistration>;
  controller?: object | null;
}) {
  Object.defineProperty(navigator, "serviceWorker", {
    value: { register: vi.fn(opts.register), controller: opts.controller ?? null },
    configurable: true,
  });
}

afterEach(() => {
  resetServiceWorkerRegistration();
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("UX-16 registerServiceWorker negative-path", () => {
  it("negative 1: 서비스워커를 지원하지 않는 브라우저에서는 등록 없이 null을 반환한다", async () => {
    // jsdom 기본 navigator에는 serviceWorker가 없다 — 실제 미지원 환경과 동형.
    expect(isServiceWorkerSupported()).toBe(false);
    const result = await registerServiceWorker();
    expect(result).toBeNull();
  });

  it("negative 2 + 실패 주입: register()가 거부되면 예외를 던지지 않고 null을 반환하며, 다음 호출이 재시도한다", async () => {
    const register = vi
      .fn()
      .mockRejectedValueOnce(new Error("sw registration failed: 404"))
      .mockResolvedValueOnce(new FakeRegistration());
    installFakeServiceWorkerContainer({ register });

    const first = await registerServiceWorker();
    expect(first).toBeNull();
    expect(register).toHaveBeenCalledTimes(1);

    const second = await registerServiceWorker();
    expect(second).not.toBeNull();
    expect(register).toHaveBeenCalledTimes(2);
  });

  it("negative 3: 최초 설치(컨트롤러 없음)에서는 installing→installed 전환이 일어나도 updateAvailable을 켜지 않는다", async () => {
    const fakeRegistration = new FakeRegistration();
    installFakeServiceWorkerContainer({
      register: async () => fakeRegistration,
      controller: null,
    });
    const onUpdateAvailable = vi.fn();

    const handle = await registerServiceWorker("/sw.js", onUpdateAvailable);
    expect(handle).not.toBeNull();

    const worker = new FakeWorker("installing");
    fakeRegistration.triggerUpdateFound(worker);
    worker.transitionTo("installed");

    expect(handle!.updateAvailable()).toBe(false);
    expect(onUpdateAvailable).not.toHaveBeenCalled();
  });

  it("기존 컨트롤러가 있는 상태(재방문)에서 새 버전이 installed되면 updateAvailable이 켜진다", async () => {
    const fakeRegistration = new FakeRegistration();
    installFakeServiceWorkerContainer({
      register: async () => fakeRegistration,
      controller: {},
    });
    const onUpdateAvailable = vi.fn();

    const handle = await registerServiceWorker("/sw.js", onUpdateAvailable);
    const worker = new FakeWorker("installing");
    fakeRegistration.triggerUpdateFound(worker);
    worker.transitionTo("installed");

    expect(handle!.updateAvailable()).toBe(true);
    expect(onUpdateAvailable).toHaveBeenCalledTimes(1);
  });
});

describe("성능 단언", () => {
  it("동시에 200번 호출돼도 실제 register()는 1회만 나가고 예산 안에 끝난다(등록 폭주 방지)", async () => {
    let registerCalls = 0;
    installFakeServiceWorkerContainer({
      register: async () => {
        registerCalls += 1;
        return new FakeRegistration();
      },
    });

    const start = performance.now();
    const results = await Promise.all(Array.from({ length: 200 }, () => registerServiceWorker()));
    const elapsedMs = performance.now() - start;

    expect(registerCalls).toBe(1);
    expect(results.every((r) => r !== null)).toBe(true);
    expect(elapsedMs).toBeLessThan(perfBudgetMs(200));
  });
});

// 게이트 적색 재현(UX-16): registerServiceWorker의 pending memoization이 없던
// "순진한" 구현(매 호출마다 그냥 navigator.serviceWorker.register를 호출)을
// naiveRegisterServiceWorker로 재구현해 동일 시나리오(동시 2회 호출)에서 대조한다.
// naive는 등록 폭주(register 2회)를 일으키고, real은 1회로 억제함을 같은 파일
// 안에서 직접 단언한다 — memoization 로직을 되돌리면 real 쪽 단언이 실제로
// FAIL하게 된다.
async function naiveRegisterServiceWorker(swUrl = "/sw.js"): Promise<FakeRegistration | null> {
  if (!isServiceWorkerSupported()) return null;
  return navigator.serviceWorker.register(swUrl) as unknown as Promise<FakeRegistration>;
}

describe("게이트 적색 재현: memoization을 되돌리면 이 대조가 적발한다", () => {
  it("naive(되돌린 상태)는 동시 2회 호출에서 register를 2번 보내고, real은 1번만 보낸다", async () => {
    const naiveRegister = vi.fn(async () => new FakeRegistration());
    installFakeServiceWorkerContainer({ register: naiveRegister });

    await Promise.all([naiveRegisterServiceWorker(), naiveRegisterServiceWorker()]);
    // naive(되돌린 상태)에서는 여기서 이미 실패를 놓친다 — 등록이 중복으로 나간다.
    expect(naiveRegister).toHaveBeenCalledTimes(2);

    Reflect.deleteProperty(navigator, "serviceWorker");
    resetServiceWorkerRegistration();

    const realRegister = vi.fn(async () => new FakeRegistration());
    installFakeServiceWorkerContainer({ register: realRegister });

    await Promise.all([registerServiceWorker(), registerServiceWorker()]);
    // 동일 시나리오에서 real은 1회로 억제한다 — 이 대조가 되돌림을 실제로 적발함을 증명.
    expect(realRegister).toHaveBeenCalledTimes(1);
  });
});
