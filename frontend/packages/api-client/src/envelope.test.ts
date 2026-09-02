import { describe, expect, it } from "vitest";
import {
  EnvelopeFormatError,
  deriveFreshness,
  resolveRetryAfterSec,
  resolveTraceId,
  unwrap,
} from "./envelope";

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

// spec §9 PLT-25: ApiError.retryAfterSec을 어떤 값에서 뽑아낼지 결정하는 규칙.
describe("resolveRetryAfterSec", () => {
  it("봉투 retry_after_seconds가 있으면 헤더보다 그 값을 우선한다", () => {
    expect(resolveRetryAfterSec(30, "60")).toBe(30);
  });

  it("봉투 값이 없으면 Retry-After 헤더로 폴백한다", () => {
    expect(resolveRetryAfterSec(null, "15")).toBe(15);
    expect(resolveRetryAfterSec(undefined, "15")).toBe(15);
  });

  it("둘 다 없으면 undefined를 반환한다(호출부가 재시도 버튼을 즉시 활성화하는 기준)", () => {
    expect(resolveRetryAfterSec(null, undefined)).toBeUndefined();
    expect(resolveRetryAfterSec(undefined, null)).toBeUndefined();
  });
});

// spec §3.3 ApiResponse.meta.as_of를 화면 표시용 신선도로 바꾸는 순수 함수.
// 판정 불가 상태(누락/파싱불가/미래시각)를 fresh로 침묵 처리하지 않는지가 핵심.
describe("deriveFreshness", () => {
  it("정상 케이스: staleAfterSec 이내면 isStale=false", () => {
    const now = new Date("2026-09-03T00:05:00Z");
    const result = deriveFreshness("2026-09-03T00:00:00Z", now, { staleAfterSec: 600 });

    expect(result.kind).toBe("ok");
    expect(result.ageSec).toBe(300);
    expect(result.isStale).toBe(false);
    expect(result.asOfDate).toEqual(new Date("2026-09-03T00:00:00Z"));
  });

  it("경계 케이스: ageSec이 staleAfterSec과 정확히 같으면 stale로 판정한다", () => {
    const now = new Date("2026-09-03T00:10:00Z");
    const result = deriveFreshness("2026-09-03T00:00:00Z", now, { staleAfterSec: 600 });

    expect(result.kind).toBe("ok");
    expect(result.ageSec).toBe(600);
    expect(result.isStale).toBe(true);
  });

  it("미래 시각: as_of가 now보다 미래면 isStale을 null(판정 불가)로 반환한다", () => {
    const now = new Date("2026-09-03T00:00:00Z");
    const result = deriveFreshness("2026-09-03T00:05:00Z", now, { staleAfterSec: 600 });

    expect(result.kind).toBe("future");
    expect(result.isStale).toBeNull();
    expect(result.ageSec).toBe(-300);
  });

  it("파싱 불가/누락: silent fallback 없이 명시적 판정 불가 상태를 반환한다", () => {
    const now = new Date("2026-09-03T00:00:00Z");

    const invalid = deriveFreshness("not-a-date", now, { staleAfterSec: 600 });
    expect(invalid.kind).toBe("invalid");
    expect(invalid.isStale).toBeNull();
    expect(invalid.asOfDate).toBeNull();
    expect(invalid.ageSec).toBeNull();

    const missing = deriveFreshness(undefined, now, { staleAfterSec: 600 });
    expect(missing.kind).toBe("missing");
    expect(missing.isStale).toBeNull();

    const empty = deriveFreshness("", now, { staleAfterSec: 600 });
    expect(empty.kind).toBe("missing");
    expect(empty.isStale).toBeNull();
  });
});
