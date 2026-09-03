import { describe, expect, it } from "vitest";
import { classifyMembershipError, describeMembershipError } from "./membershipMutation";

function apiErrorLike(statusCode: number, errorCode: string, message = "internal detail"): unknown {
  return { statusCode, errorCode, message };
}

describe("classifyMembershipError", () => {
  it("마지막 owner revoke: 409 STATE_INVALID_TRANSITION -> last_owner_denied", () => {
    expect(classifyMembershipError(apiErrorLike(409, "STATE_INVALID_TRANSITION"))).toBe("last_owner_denied");
  });

  it("마지막 owner 거부가 403 POLICY_*로 오는 경우도 last_owner_denied로 받는다(방어적 매핑)", () => {
    expect(classifyMembershipError(apiErrorLike(403, "POLICY_LAST_OWNER"))).toBe("last_owner_denied");
  });

  it("마지막 owner 거부가 403 RISK_*로 와도 last_owner_denied다", () => {
    expect(classifyMembershipError(apiErrorLike(403, "RISK_LAST_OWNER"))).toBe("last_owner_denied");
  });

  it("다른 테넌트 membership_id: 403 AUTH_TENANT_MISMATCH -> cross_tenant_denied", () => {
    expect(classifyMembershipError(apiErrorLike(403, "AUTH_TENANT_MISMATCH"))).toBe("cross_tenant_denied");
  });

  it("자기 자신 revoke: 403 AUTHZ_FORBIDDEN -> action_forbidden(last_owner_denied·cross_tenant_denied와 구분)", () => {
    expect(classifyMembershipError(apiErrorLike(403, "AUTHZ_FORBIDDEN"))).toBe("action_forbidden");
  });

  it("이미 폐기: 404 RESOURCE_NOT_FOUND -> already_revoked", () => {
    expect(classifyMembershipError(apiErrorLike(404, "RESOURCE_NOT_FOUND"))).toBe("already_revoked");
  });

  it("statusCode만 404고 errorCode가 없어도 already_revoked다", () => {
    expect(classifyMembershipError({ statusCode: 404 })).toBe("already_revoked");
  });

  it("네 갈래 모두 서로 다른 분류다(자기 자신 revoke·마지막 owner revoke·다른 테넌트 membership_id·이미 폐기)", () => {
    const results = new Set([
      classifyMembershipError(apiErrorLike(403, "AUTHZ_FORBIDDEN")),
      classifyMembershipError(apiErrorLike(409, "STATE_INVALID_TRANSITION")),
      classifyMembershipError(apiErrorLike(403, "AUTH_TENANT_MISMATCH")),
      classifyMembershipError(apiErrorLike(404, "RESOURCE_NOT_FOUND")),
    ]);
    expect(results.size).toBe(4);
  });

  it("알려지지 않은 에러(500 INTERNAL_ERROR)는 unknown_denied로 수렴한다(throw 금지)", () => {
    expect(classifyMembershipError(apiErrorLike(500, "INTERNAL_ERROR"))).toBe("unknown_denied");
  });

  it("null/undefined/일반 Error도 throw 없이 unknown_denied다", () => {
    expect(classifyMembershipError(null)).toBe("unknown_denied");
    expect(classifyMembershipError(undefined)).toBe("unknown_denied");
    expect(classifyMembershipError(new Error("boom"))).toBe("unknown_denied");
  });

  it("반환값은 reason 문자열뿐 — err.message를 그대로 노출하지 않는다", () => {
    const err = apiErrorLike(409, "STATE_INVALID_TRANSITION", "매우 민감한 내부 상세");
    const reason = classifyMembershipError(err);
    expect(reason).toBe("last_owner_denied");
    expect(String(reason)).not.toContain("민감");
  });
});

describe("describeMembershipError", () => {
  it("5개 reason 전부 비어있지 않은 한국어 문구를 반환한다", () => {
    const reasons = [
      "last_owner_denied",
      "cross_tenant_denied",
      "action_forbidden",
      "already_revoked",
      "unknown_denied",
    ] as const;
    for (const reason of reasons) {
      expect(describeMembershipError(reason).length).toBeGreaterThan(0);
    }
  });

  it("reason별 문구는 서로 다르다", () => {
    const reasons = [
      "last_owner_denied",
      "cross_tenant_denied",
      "action_forbidden",
      "already_revoked",
      "unknown_denied",
    ] as const;
    const messages = new Set(reasons.map(describeMembershipError));
    expect(messages.size).toBe(reasons.length);
  });
});
