import { afterEach, describe, expect, it, vi } from "vitest";

// task-3633: CI가 "window는 있고 localStorage가 undefined"인 환경에서
// useAuthStore 모듈 로드 자체가 TypeError로 죽는 것을 재현했다
// (esc-ci-88205564777f, 78b00a3d3c85부터 연속). 모듈 최상단 store 초기화가
// localStorage.getItem을 무조건 호출해서 생긴 문제이므로, 거부 입력(localStorage
// undefined)을 직접 스텁해 import 시점에 예외 없이 token=null로 초기화됨을 고정한다.
describe("useAuthStore — localStorage undefined 환경 모듈 로드 가드(task-3633)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("window는 정의돼 있고 localStorage가 undefined이면 import가 TypeError 없이 token=null로 초기화된다", async () => {
    expect(typeof window).not.toBe("undefined");
    vi.stubGlobal("localStorage", undefined);

    const { useAuthStore } = await import("@aios/shared-hooks");

    expect(useAuthStore.getState().token).toBeNull();
  });
});
