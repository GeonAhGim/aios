import { describe, expect, it } from "vitest";
import { parseTokenPair, shouldPreRefresh } from "./tokenPair";

function validPair(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    access_token: "access-abc",
    refresh_token: "refresh-xyz",
    token_type: "bearer",
    expires_in: 900,
    session_id: "session-1",
    ...overrides,
  };
}

describe("parseTokenPair", () => {
  it("정상 §3.4 TokenPairResponse를 camelCase ParsedTokenPair로 파싱한다", () => {
    expect(parseTokenPair(validPair())).toEqual({
      accessToken: "access-abc",
      refreshToken: "refresh-xyz",
      expiresIn: 900,
      sessionId: "session-1",
    });
  });

  it.each(["access_token", "refresh_token", "session_id"])(
    "%s 필드가 누락되면 null이다(throw 금지)",
    (field) => {
      const pair = validPair();
      delete pair[field];
      expect(parseTokenPair(pair)).toBeNull();
    },
  );

  it("token_type이 'bearer'가 아니면 null이다", () => {
    expect(parseTokenPair(validPair({ token_type: "mac" }))).toBeNull();
  });

  it("expires_in이 0 이하이면 null이다", () => {
    expect(parseTokenPair(validPair({ expires_in: 0 }))).toBeNull();
    expect(parseTokenPair(validPair({ expires_in: -1 }))).toBeNull();
  });

  it("data가 객체가 아니면 null이다", () => {
    expect(parseTokenPair(null)).toBeNull();
    expect(parseTokenPair("bearer-token")).toBeNull();
  });
});

describe("shouldPreRefresh", () => {
  it("만료 skewSec(기본 60초) 전이면 true다", () => {
    const now = 1_000_000;
    expect(shouldPreRefresh(now + 60_000, now)).toBe(true);
  });

  it("만료까지 skewSec보다 여유가 있으면 false다", () => {
    const now = 1_000_000;
    expect(shouldPreRefresh(now + 61_000, now)).toBe(false);
  });

  it("이미 만료된 경우에도 true다", () => {
    const now = 1_000_000;
    expect(shouldPreRefresh(now - 1, now)).toBe(true);
  });
});
