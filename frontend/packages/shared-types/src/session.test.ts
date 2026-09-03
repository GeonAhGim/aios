import { describe, expect, it } from "vitest";
import { canRevoke, isCurrentSession, parseSessionView, type ParsedSessionView } from "./session";

function validView(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    sessionId: "session-1",
    createdAt: "2026-09-01T00:00:00Z",
    lastSeenAt: "2026-09-02T00:00:00Z",
    userAgent: "Mozilla/5.0",
    ip: "203.0.113.10",
    revokedAt: null,
    ...overrides,
  };
}

describe("parseSessionView", () => {
  it("정상 §3.4 세션 뷰를 화이트리스트 파싱한다", () => {
    expect(parseSessionView(validView())).toEqual({
      sessionId: "session-1",
      createdAt: "2026-09-01T00:00:00Z",
      lastSeenAt: "2026-09-02T00:00:00Z",
      userAgent: "Mozilla/5.0",
      ip: "203.0.113.10",
      revokedAt: null,
    });
  });

  it("화이트리스트에 없는 미지 필드는 무시한다", () => {
    const parsed = parseSessionView(validView({ tenantId: "tenant-9", internalFlag: true }));
    expect(parsed).toEqual({
      sessionId: "session-1",
      createdAt: "2026-09-01T00:00:00Z",
      lastSeenAt: "2026-09-02T00:00:00Z",
      userAgent: "Mozilla/5.0",
      ip: "203.0.113.10",
      revokedAt: null,
    });
  });

  it.each(["sessionId", "createdAt", "lastSeenAt"])(
    "%s 필드가 누락되면 null이다(throw 금지)",
    (field) => {
      const view = validView();
      delete view[field];
      expect(parseSessionView(view)).toBeNull();
    },
  );

  it.each(["sessionId", "createdAt", "lastSeenAt"])("%s가 빈 문자열이면 null이다", (field) => {
    expect(parseSessionView(validView({ [field]: "" }))).toBeNull();
  });

  it("userAgent/ip가 없거나 null이면 null로 취급한다(throw 금지)", () => {
    const view = validView({ userAgent: null, ip: null });
    expect(parseSessionView(view)).toMatchObject({ userAgent: null, ip: null });

    const view2 = validView();
    delete view2.userAgent;
    delete view2.ip;
    expect(parseSessionView(view2)).toMatchObject({ userAgent: null, ip: null });
  });

  it("revokedAt이 타임스탬프이면 그대로 보존한다", () => {
    const parsed = parseSessionView(validView({ revokedAt: "2026-09-03T00:00:00Z" }));
    expect(parsed?.revokedAt).toBe("2026-09-03T00:00:00Z");
  });

  it("userAgent/ip/revokedAt이 문자열·null이 아닌 값이면 null이다", () => {
    expect(parseSessionView(validView({ ip: 12345 }))).toBeNull();
    expect(parseSessionView(validView({ revokedAt: 12345 }))).toBeNull();
  });

  it("raw가 객체가 아니면 null이다", () => {
    expect(parseSessionView(null)).toBeNull();
    expect(parseSessionView("session-1")).toBeNull();
    expect(parseSessionView(undefined)).toBeNull();
  });
});

describe("canRevoke", () => {
  it("revokedAt이 null이면 폐기 가능하다", () => {
    const view = parseSessionView(validView()) as ParsedSessionView;
    expect(canRevoke(view)).toBe(true);
  });

  // negative test: 이미 폐기된(revokedAt 있는) 세션은 목록에서 다시 폐기할 수 없다.
  it("negative: revokedAt이 있으면 이미 폐기된 세션이라 다시 폐기할 수 없다", () => {
    const view = parseSessionView(validView({ revokedAt: "2026-09-03T00:00:00Z" })) as ParsedSessionView;
    expect(canRevoke(view)).toBe(false);
  });
});

describe("isCurrentSession", () => {
  const view = parseSessionView(validView()) as ParsedSessionView;

  it("currentSessionId가 세션 id와 일치하면 true다", () => {
    expect(isCurrentSession(view, "session-1")).toBe(true);
  });

  it("currentSessionId가 다르면 false다", () => {
    expect(isCurrentSession(view, "session-2")).toBe(false);
  });

  it("currentSessionId가 null이면 false다", () => {
    expect(isCurrentSession(view, null)).toBe(false);
  });
});
