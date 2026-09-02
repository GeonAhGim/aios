import { describe, expect, it } from "vitest";
import { createTokenStore } from "./tokenStore";

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
