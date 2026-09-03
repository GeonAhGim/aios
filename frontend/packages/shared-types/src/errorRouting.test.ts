// task-483: routeApiError가 apiError.ts의 ApiErrorCode 유니온 24개 전부를 정확히 한
// 갈래로 라우팅하는지 고정한다. CASES는 `Record<ApiErrorCode, ...>`로 선언했으므로 이후
// apiError.ts에 error_code가 하나 추가되면 이 파일이 컴파일조차 되지 않는다(누락된 키가
// 있으면 TS2741/TS2739) — 그래서 "코드 추가 시 갈래를 안 만들면 실패한다"를 컴파일
// 단계에서 잡는다. 각 케이스의 kind 단언은 그 코드가 실제로 "unknown"(폴백)이 아니라
// 의도한 갈래로 떨어지는지를 런타임에서 잡는다.
import { describe, expect, it } from "vitest";
import type { ApiErrorCode } from "./apiError";
import { routeApiError, type RoutedApiErrorKind } from "./errorRouting";
import { classifyBadRequest } from "./badRequest";
import { classifyStateConflict } from "./stateConflict";

interface RoutingCase {
  statusCode: number;
  expectedKind: RoutedApiErrorKind;
  extra?: Record<string, unknown>;
}

// §3.3 표의 HTTP status 그대로. extra는 그 갈래가 실제로 payload를 채우는 데 필요한
// 필드만(예: VALIDATION_INVALID_FIELD의 details.fields, AUTH_ACCOUNT_LOCKED의
// retryAfterSec) — 나머지는 routeApiError가 statusCode/errorCode만으로 판정한다.
const CASES: Record<ApiErrorCode, RoutingCase> = {
  VALIDATION_INVALID_FIELD: {
    statusCode: 400,
    expectedKind: "field_errors",
    extra: { error_code: "VALIDATION_INVALID_FIELD", details: { fields: ["body.email"] } },
  },
  VALIDATION_IDEMPOTENCY_KEY_REQUIRED: { statusCode: 400, expectedKind: "idempotency_missing_header" },
  VALIDATION_DISCLOSURE_RETIRED: { statusCode: 400, expectedKind: "disclosure_retired" },
  AUTH_REQUIRED: { statusCode: 401, expectedKind: "auth_required" },
  AUTH_INVALID_CREDENTIALS: { statusCode: 401, expectedKind: "auth_required" },
  AUTH_TOKEN_EXPIRED: { statusCode: 401, expectedKind: "auth_required" },
  AUTH_TOKEN_INVALID: { statusCode: 401, expectedKind: "auth_required" },
  AUTH_SESSION_REVOKED: { statusCode: 401, expectedKind: "auth_required" },
  AUTH_ACCOUNT_LOCKED: { statusCode: 423, expectedKind: "account_locked", extra: { retryAfterSec: 30 } },
  AUTH_MFA_REQUIRED: { statusCode: 403, expectedKind: "mfa_required" },
  AUTH_MFA_INVALID: { statusCode: 400, expectedKind: "mfa_invalid" },
  AUTH_TENANT_MISMATCH: { statusCode: 403, expectedKind: "tenant_mismatch" },
  AUTHZ_FORBIDDEN: { statusCode: 403, expectedKind: "forbidden" },
  AUTHZ_ZONE_VIOLATION: { statusCode: 403, expectedKind: "forbidden" },
  POLICY_LIVE_BLOCKED: { statusCode: 403, expectedKind: "policy_denied" },
  RESOURCE_NOT_FOUND: { statusCode: 404, expectedKind: "not_found" },
  STATE_CONCURRENCY_CONFLICT: { statusCode: 409, expectedKind: "refetch_retry" },
  STATE_INVALID_TRANSITION: { statusCode: 409, expectedKind: "invalid_transition" },
  INTEGRITY_IDEMPOTENCY_CONFLICT: { statusCode: 409, expectedKind: "idempotency_new_key" },
  RATE_LIMIT_EXCEEDED: { statusCode: 429, expectedKind: "backoff_retry", extra: { retryAfterSec: 5 } },
  EXCHANGE_UNAVAILABLE: { statusCode: 503, expectedKind: "backoff_retry" },
  EXCHANGE_FATAL: { statusCode: 502, expectedKind: "server_fatal", extra: { traceId: "trace-fatal" } },
  DEPENDENCY_NOT_READY: { statusCode: 503, expectedKind: "backoff_retry" },
  INTERNAL_ERROR: { statusCode: 500, expectedKind: "server_fatal", extra: { traceId: "trace-internal" } },
};

function buildErr(errorCode: string, { statusCode, extra }: RoutingCase): Record<string, unknown> {
  return { statusCode, errorCode, ...extra };
}

describe("routeApiError — §3.3 error taxonomy exhaustiveness(task-483)", () => {
  for (const [errorCode, testCase] of Object.entries(CASES) as Array<[ApiErrorCode, RoutingCase]>) {
    it(`${errorCode}(${testCase.statusCode})는 ${testCase.expectedKind}로 라우팅한다`, () => {
      const result = routeApiError(buildErr(errorCode, testCase));
      expect(result.kind).toBe(testCase.expectedKind);
      expect(result.kind).not.toBe("unknown");
    });
  }
});

describe("routeApiError — 우선순위 규칙(task-483)", () => {
  it("INTEGRITY_IDEMPOTENCY_CONFLICT(409)는 classifyStateConflict도 idempotency로 태깅하지만, routeApiError는 idempotency_new_key로 확정한다", () => {
    const err = { statusCode: 409, errorCode: "INTEGRITY_IDEMPOTENCY_CONFLICT" };
    expect(classifyStateConflict(err)).toBe("idempotency");
    expect(routeApiError(err)).toEqual({ kind: "idempotency_new_key" });
  });

  it("VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400)는 classifyBadRequest도 idempotency_key_required로 잡지만, routeApiError는 idempotency_missing_header로 확정한다", () => {
    const err = { statusCode: 400, errorCode: "VALIDATION_IDEMPOTENCY_KEY_REQUIRED" };
    expect(classifyBadRequest(err)).toBe("idempotency_key_required");
    expect(routeApiError(err)).toEqual({ kind: "idempotency_missing_header" });
  });
});

describe("routeApiError — payload 위임(task-483)", () => {
  it("field_errors kind는 extractFieldErrors의 결과를 그대로 담는다", () => {
    const result = routeApiError(buildErr("VALIDATION_INVALID_FIELD", CASES.VALIDATION_INVALID_FIELD));
    expect(result).toEqual({ kind: "field_errors", fieldErrors: { email: "입력값을 확인해주세요." } });
  });

  it("policy_denied kind는 extractReasonCodes의 결과를 그대로 담는다", () => {
    const err = {
      statusCode: 403,
      errorCode: "POLICY_LIVE_BLOCKED",
      error_code: "POLICY_LIVE_BLOCKED",
      details: { reason_codes: ["POLICY_LIVE_BLOCKED"] },
    };
    expect(routeApiError(err)).toEqual({ kind: "policy_denied", reasonCodes: ["POLICY_LIVE_BLOCKED"] });
  });

  it("account_locked kind는 deriveLockout의 retryAfterSec을 그대로 담는다", () => {
    const result = routeApiError(buildErr("AUTH_ACCOUNT_LOCKED", CASES.AUTH_ACCOUNT_LOCKED));
    expect(result).toEqual({ kind: "account_locked", retryAfterSec: 30 });
  });

  it("backoff_retry kind는 classifyRetry의 afterSec을 그대로 담는다(RATE_LIMIT_EXCEEDED)", () => {
    const result = routeApiError(buildErr("RATE_LIMIT_EXCEEDED", CASES.RATE_LIMIT_EXCEEDED));
    expect(result).toEqual({ kind: "backoff_retry", afterSec: 5 });
  });

  it("server_fatal kind는 classifyServerError의 traceId를 그대로 담는다(EXCHANGE_FATAL)", () => {
    const result = routeApiError(buildErr("EXCHANGE_FATAL", CASES.EXCHANGE_FATAL));
    expect(result).toEqual({ kind: "server_fatal", traceId: "trace-fatal" });
  });
});

describe("routeApiError — 미지 코드 fallback(negative test, task-483)", () => {
  it("아는 status/errorCode 조합이 아니면 unknown으로 폴백한다(throw 금지)", () => {
    expect(routeApiError({ statusCode: 418, errorCode: "TEAPOT_UNKNOWN" })).toEqual({ kind: "unknown" });
    expect(routeApiError({ statusCode: 400, errorCode: "VALIDATION_FUTURE_CODE" })).toEqual({
      kind: "unknown",
    });
    expect(routeApiError({ statusCode: 403, errorCode: "AUTHZ_FUTURE_CODE" }).kind).toBe("forbidden");
  });

  it("ApiError 모양이 아닌 값(null·문자열·일반 Error)도 throw 없이 unknown으로 수렴한다", () => {
    expect(routeApiError(null)).toEqual({ kind: "unknown" });
    expect(routeApiError(undefined)).toEqual({ kind: "unknown" });
    expect(routeApiError("boom")).toEqual({ kind: "unknown" });
    expect(routeApiError(new Error("boom"))).toEqual({ kind: "unknown" });
  });
});
