import { describe, expect, it } from "vitest";
import { classifyBadRequest } from "./badRequest";

describe("classifyBadRequest", () => {
  it("400 VALIDATION_INVALID_FIELD는 field로 분류한다", () => {
    expect(
      classifyBadRequest({ statusCode: 400, errorCode: "VALIDATION_INVALID_FIELD" }),
    ).toBe("field");
  });

  it("400 VALIDATION_IDEMPOTENCY_KEY_REQUIRED는 idempotency_key_required로 분류한다", () => {
    expect(
      classifyBadRequest({ statusCode: 400, errorCode: "VALIDATION_IDEMPOTENCY_KEY_REQUIRED" }),
    ).toBe("idempotency_key_required");
  });

  it("400 VALIDATION_DISCLOSURE_RETIRED는 disclosure_retired로 분류한다", () => {
    expect(
      classifyBadRequest({ statusCode: 400, errorCode: "VALIDATION_DISCLOSURE_RETIRED" }),
    ).toBe("disclosure_retired");
  });

  it("400 AUTH_MFA_INVALID는 mfa_invalid로 분류한다", () => {
    expect(classifyBadRequest({ statusCode: 400, errorCode: "AUTH_MFA_INVALID" })).toBe(
      "mfa_invalid",
    );
  });

  it("알 수 없는 400 코드는 unknown으로 폴백한다(throw 금지)", () => {
    expect(classifyBadRequest({ statusCode: 400, errorCode: "SOME_FUTURE_CODE" })).toBe(
      "unknown",
    );
    expect(classifyBadRequest({ statusCode: 400 })).toBe("unknown");
  });

  it("400이 아니면 null이다", () => {
    expect(
      classifyBadRequest({ statusCode: 403, errorCode: "AUTHZ_FORBIDDEN" }),
    ).toBeNull();
    expect(classifyBadRequest({ statusCode: 500, errorCode: "INTERNAL_ERROR" })).toBeNull();
  });

  it("statusCode가 없는 값(일반 Error·null·문자열)은 null이다", () => {
    expect(classifyBadRequest(new Error("boom"))).toBeNull();
    expect(classifyBadRequest(null)).toBeNull();
    expect(classifyBadRequest("VALIDATION_INVALID_FIELD")).toBeNull();
  });
});
