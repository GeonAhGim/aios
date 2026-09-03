import { afterEach, describe, expect, it, vi } from "vitest";
import { createTokenStore } from "./tokenStore";
import { configureTokenRefreshHandler, refreshAccessToken } from "./tokenRefresh";

function pair(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    access_token: "access-1",
    refresh_token: "refresh-1",
    token_type: "bearer",
    expires_in: 900,
    session_id: "session-1",
    ...overrides,
  };
}

describe("createTokenStore", () => {
  it("setPair 후 getAccess/getRefresh/peekSessionId가 저장된 값을 낸다", () => {
    const store = createTokenStore();

    const parsed = store.setPair(pair());

    expect(parsed).toEqual({
      accessToken: "access-1",
      refreshToken: "refresh-1",
      expiresIn: 900,
      sessionId: "session-1",
    });
    expect(store.getAccess()).toBe("access-1");
    expect(store.getRefresh()).toBe("refresh-1");
    expect(store.peekSessionId()).toBe("session-1");
  });

  it("§3.4 계약 위반 데이터는 저장하지 않고 null을 반환한다", () => {
    const store = createTokenStore();

    expect(store.setPair({ access_token: "only-this" })).toBeNull();
    expect(store.getAccess()).toBeNull();
  });

  it("toJSON/toString은 refresh_token 원문을 절대 노출하지 않는다", () => {
    const store = createTokenStore();
    store.setPair(pair({ refresh_token: "super-secret-refresh" }));

    const json = store.toJSON();
    const serialized = store.toString();

    expect(JSON.stringify(json)).not.toContain("super-secret-refresh");
    expect(serialized).not.toContain("super-secret-refresh");
    expect(json.refreshToken).not.toBe("super-secret-refresh");
  });

  it("회전: setPair 재호출 시 이전 refresh_token은 즉시 폐기되고 getRefresh는 새 값만 낸다", () => {
    const store = createTokenStore();
    store.setPair(pair({ refresh_token: "refresh-old", access_token: "access-old" }));

    store.setPair(pair({ refresh_token: "refresh-new", access_token: "access-new" }));

    expect(store.getRefresh()).toBe("refresh-new");
    expect(store.getRefresh()).not.toBe("refresh-old");
    expect(store.getAccess()).toBe("access-new");
  });

  it("clear는 모든 필드를 초기화한다", () => {
    const store = createTokenStore();
    store.setPair(pair());

    store.clear();

    expect(store.getAccess()).toBeNull();
    expect(store.getRefresh()).toBeNull();
    expect(store.peekSessionId()).toBeNull();
  });
});

// task-955: tokenStore(task-426) ↔ tokenRefresh(task-386) 배선. 모듈 싱글턴
// (refreshHandler/inFlightRefresh) 상태가 테스트 간에 새지 않도록 매번
// 초기화한다(tokenRefresh.test.ts와 동일한 관용).
describe("createTokenStore ↔ tokenRefresh 선제 갱신 배선", () => {
  afterEach(() => {
    configureTokenRefreshHandler(null);
    vi.useRealTimers();
  });

  it("만료 skewSec(60초) 전에 refreshAccessToken()을 1회 선제 호출한다", async () => {
    vi.useFakeTimers();
    const handler = vi.fn().mockResolvedValue(true);
    configureTokenRefreshHandler(handler);
    const store = createTokenStore();

    // expires_in=61초 → skew(60초) 전 시점까지 지연 1초.
    store.setPair(pair({ expires_in: 61 }));
    expect(handler).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1_000);

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("이미 만료 임박(skew 이내)한 pair는 즉시 선제 갱신을 예약한다", async () => {
    vi.useFakeTimers();
    const handler = vi.fn().mockResolvedValue(true);
    configureTokenRefreshHandler(handler);
    const store = createTokenStore();

    store.setPair(pair({ expires_in: 30 }));
    await vi.advanceTimersByTimeAsync(0);

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("타이머가 쏜 선제 갱신과 동시에 진행 중인(401발) refresh는 같은 in-flight promise를 공유해 1회만 실행된다", async () => {
    vi.useFakeTimers();
    let resolveHandler: (value: boolean) => void = () => {};
    const handler = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveHandler = resolve;
        }),
    );
    configureTokenRefreshHandler(handler);
    const store = createTokenStore();

    // http.ts의 401 AUTH_TOKEN_EXPIRED 경로가 이미 refresh를 진행 중이라고 가정.
    const inFlightFrom401 = refreshAccessToken();
    expect(handler).toHaveBeenCalledTimes(1);

    // 그 사이 선제 갱신 타이머가 쏜다 — 이미 진행 중인 것과 같은 promise를 공유해야 한다.
    store.setPair(pair({ expires_in: 30 }));
    await vi.advanceTimersByTimeAsync(0);

    expect(handler).toHaveBeenCalledTimes(1);
    resolveHandler(true);
    await expect(inFlightFrom401).resolves.toBe(true);
  });

  it("clear()는 예약된 선제 갱신 타이머를 취소한다", async () => {
    vi.useFakeTimers();
    const handler = vi.fn().mockResolvedValue(true);
    configureTokenRefreshHandler(handler);
    const store = createTokenStore();

    store.setPair(pair({ expires_in: 900 }));
    store.clear();

    await vi.advanceTimersByTimeAsync(900_000);

    expect(handler).not.toHaveBeenCalled();
  });

  it("setPair 재호출(회전)은 이전 타이머를 취소하고 새 만료 기준으로 다시 예약한다", async () => {
    vi.useFakeTimers();
    const handler = vi.fn().mockResolvedValue(true);
    configureTokenRefreshHandler(handler);
    const store = createTokenStore();

    store.setPair(pair({ expires_in: 61 }));
    await vi.advanceTimersByTimeAsync(500);
    // 이전 타이머(1초 후 발사 예정)가 절반쯤 지났을 때 회전 — 새 타이머는
    // 다시 61초 skew 기준(1초 지연)으로 걸린다.
    store.setPair(pair({ expires_in: 61 }));
    await vi.advanceTimersByTimeAsync(500);

    expect(handler).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(500);

    expect(handler).toHaveBeenCalledTimes(1);
  });
});
