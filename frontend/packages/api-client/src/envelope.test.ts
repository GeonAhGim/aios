import { describe, expect, it } from "vitest";
import { EnvelopeFormatError, resolveTraceId, unwrap } from "./envelope";

describe("unwrap", () => {
  it("성공 응답에서 data와 meta를 꺼낸다", () => {
    const body = {
      data: { id: 1 },
      meta: { trace_id: "t-1", as_of: "2026-09-03T00:00:00Z", page: null },
    };

    const result = unwrap<{ id: number }>(body);

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("unreachable");
    expect(result.data).toEqual({ id: 1 });
    expect(result.meta.trace_id).toBe("t-1");
    expect(result.meta.page).toBeNull();
  });

  it("페이지네이션 meta도 그대로 통과시킨다", () => {
    const body = {
      data: [1, 2, 3],
      meta: {
        trace_id: "t-2",
        as_of: "2026-09-03T00:00:00Z",
        page: { total: 10, page: 1, size: 3, next_cursor: "abc" },
      },
    };

    const result = unwrap<number[]>(body);

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("unreachable");
    expect(result.data).toEqual([1, 2, 3]);
    expect(result.meta.page).toEqual({ total: 10, page: 1, size: 3, next_cursor: "abc" });
  });

  it("에러 봉투는 ok=false와 error를 반환한다", () => {
    const body = {
      error_code: "VALIDATION_INVALID_FIELD",
      message: "잘못된 입력입니다.",
      details: { fields: ["email"] },
      trace_id: "t-3",
      retry_after_seconds: null,
    };

    const result = unwrap(body);

    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("unreachable");
    expect(result.error.error_code).toBe("VALIDATION_INVALID_FIELD");
    expect(result.error.retry_after_seconds).toBeNull();
  });

  it("재시도 대기 시간이 있는 에러 봉투도 파싱한다", () => {
    const body = {
      error_code: "RATE_LIMIT_EXCEEDED",
      message: "요청이 너무 많습니다.",
      details: {},
      trace_id: "t-4",
      retry_after_seconds: 30,
    };

    const result = unwrap(body);

    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("unreachable");
    expect(result.error.retry_after_seconds).toBe(30);
  });

  it("봉투 형식이 아니면 EnvelopeFormatError를 던진다", () => {
    expect(() => unwrap({ foo: "bar" })).toThrow(EnvelopeFormatError);
    expect(() => unwrap(null)).toThrow(EnvelopeFormatError);
    expect(() => unwrap(undefined)).toThrow(EnvelopeFormatError);
    expect(() => unwrap("plain string")).toThrow(EnvelopeFormatError);
    expect(() => unwrap(42)).toThrow(EnvelopeFormatError);
  });
});

// spec §9 PLT-05/13: ApiError.traceId로 노출할 지원코드를 봉투 trace_id와
// 응답 헤더 X-Trace-Id 중에서 고르는 규칙.
describe("resolveTraceId", () => {
  it("둘 다 있으면 봉투 쪽을 우선한다", () => {
    expect(resolveTraceId("env-trace", "header-trace")).toBe("env-trace");
  });

  it("봉투에 없으면 헤더 값을 쓴다", () => {
    expect(resolveTraceId(undefined, "header-trace")).toBe("header-trace");
    expect(resolveTraceId(null, "header-trace")).toBe("header-trace");
    expect(resolveTraceId("", "header-trace")).toBe("header-trace");
  });

  it("헤더에 없으면 봉투 값을 쓴다", () => {
    expect(resolveTraceId("env-trace", undefined)).toBe("env-trace");
  });

  it("둘 다 없으면 undefined를 반환하고 예외를 던지지 않는다(legacy 에러 응답 negative)", () => {
    expect(() => resolveTraceId(undefined, undefined)).not.toThrow();
    expect(resolveTraceId(undefined, undefined)).toBeUndefined();
    expect(resolveTraceId(null, null)).toBeUndefined();
  });
});
