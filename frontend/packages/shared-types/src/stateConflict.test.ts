import { describe, expect, it } from "vitest";
import { classifyStateConflict } from "./stateConflict";

describe("classifyStateConflict", () => {
  it("409 STATE_CONCURRENCY_CONFLICT는 refetch_retry로 분류한다", () => {
    expect(
      classifyStateConflict({ statusCode: 409, errorCode: "STATE_CONCURRENCY_CONFLICT" }),
    ).toBe("refetch_retry");
  });

  it("409 STATE_INVALID_TRANSITION은 invalid_transition으로 분류한다", () => {
    expect(
      classifyStateConflict({ statusCode: 409, errorCode: "STATE_INVALID_TRANSITION" }),
    ).toBe("invalid_transition");
  });

  it("409 INTEGRITY_IDEMPOTENCY_CONFLICT는 idempotency로 분류한다", () => {
    expect(
      classifyStateConflict({ statusCode: 409, errorCode: "INTEGRITY_IDEMPOTENCY_CONFLICT" }),
    ).toBe("idempotency");
  });

  it("알 수 없는 409 코드는 invalid_transition으로 폴백한다(throw 금지)", () => {
    expect(classifyStateConflict({ statusCode: 409, errorCode: "SOME_FUTURE_CODE" })).toBe(
      "invalid_transition",
    );
    expect(classifyStateConflict({ statusCode: 409 })).toBe("invalid_transition");
  });

  it("409가 아니면 null이다", () => {
    expect(
      classifyStateConflict({ statusCode: 400, errorCode: "VALIDATION_INVALID_FIELD" }),
    ).toBeNull();
    expect(classifyStateConflict({ statusCode: 500, errorCode: "INTERNAL_ERROR" })).toBeNull();
  });

  it("statusCode가 없는 값(일반 Error·null·문자열)은 null이다", () => {
    expect(classifyStateConflict(new Error("boom"))).toBeNull();
    expect(classifyStateConflict(null)).toBeNull();
    expect(classifyStateConflict("STATE_CONCURRENCY_CONFLICT")).toBeNull();
  });
});
