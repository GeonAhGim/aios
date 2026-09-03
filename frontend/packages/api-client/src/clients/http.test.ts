import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
import { ApiClientBase, ApiError } from "../http";
import { withAccount } from "./account";
import { withAdmin } from "./admin";
import { withAuth } from "./auth";
import { withExchange } from "./exchange";

// task-1159 배치1: account/admin/auth/exchange의 resolvePath 호출부(치환·쿼리가
// 없는 것은 requestByRoute, 있는 것은 resolveEnvelope+수동 조립)를 apiPaths.ts
// 레지스트리 단일 출처로 옮긴 배선을 증명한다. 각 라우트마다 (1) 요청 URL이
// 이관 전과 바이트 동일한지, (2) 봉투/비봉투 파싱 결과가 동일한지, (3) 최소
// 1건의 negative(에러) 케이스를 fetch mock으로 직접 단언한다 — INVARIANTS
// I-01~I-11 중 이 리프가 만지는 부분(경로 하드코딩 금지·에러 분류 재사용)은
// apiPaths.clientsScan.test.ts(하드코딩 금지)와 shared-types의 classifyForbidden/
// classifyServerError 재사용(routeApiError=task-483)으로 이미 고정돼 있다.
class TestClient extends withAccount(withAdmin(withAuth(withExchange(ApiClientBase)))) {}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): TestClient {
  return new TestClient("https://api.example.test", () => null);
}

function urlOf(fetchMock: ReturnType<typeof vi.fn>): string {
  return (fetchMock.mock.calls[0] as [string, RequestInit])[0];
}

function envelopeOk(data: unknown) {
  return { data, meta: { trace_id: "t1", as_of: "2026-09-04T00:00:00Z", page: null } };
}

// 200 status라도 body가 error_code 봉투면 requestEnvelope는 즉시 실패시킨다
// (executeRequestEnvelope는 status 판정보다 unwrap을 먼저 한다) — request()라면
// 이 body를 그대로 "성공"으로 파싱해버렸을 것이므로, 두 경로의 차이를 가장
// 직접적으로 드러내는 negative 케이스다.
function envelopeErr(errorCode: string) {
  return { error_code: errorCode, message: "실패", details: {}, trace_id: "t1", retry_after_seconds: null };
}

describe("task-1159 배치1 대상 라우트: apiPaths.ts envelope 값 회귀 가드", () => {
  it.each([
    ["account.riskProfile", false],
    ["account.riskProfileHistory", false],
    ["account.approvalSettings", true],
    ["account.whitelist", true],
    ["account.approvalRequests.list", true],
    ["admin.verificationQueue", true],
    ["admin.disputes.list", true],
    ["admin.disputes.get", true],
    ["admin.users.list", true],
    ["admin.wallet.topupsPending", true],
    ["admin.approvalRequests.pending", true],
    ["auth.me", true],
    ["exchange.credentials.base", false],
    ["exchange.credentials.balance", false],
    ["exchange.credentials.capabilities", false],
  ] as const)("%s: envelope=%s", (routeName, expected) => {
    expect(API_ROUTES[routeName].envelope).toBe(expected);
  });
});

describe("account — requestByRoute 배선(비봉투 2건 + 봉투 3건)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("getRiskProfile: 비봉투 GET — URL·camelCase 파싱이 이관 전과 동일하다", async () => {
    const fetchMock = stubFetch({
      risk_profile: "중립형",
      assessed_at: "2026-09-01T00:00:00Z",
      next_reassessment_due: "2027-09-01T00:00:00Z",
      is_higher_risk_than_previous: false,
    });

    const result = await makeClient().getRiskProfile();

    expect(urlOf(fetchMock)).toBe("https://api.example.test/users/me/risk-profile");
    expect(result).toEqual({
      riskProfile: "중립형",
      assessedAt: "2026-09-01T00:00:00Z",
      nextReassessmentDue: "2027-09-01T00:00:00Z",
      isHigherRiskThanPrevious: false,
    });
  });

  it("getRiskProfile: negative — 404는 request() 경로답게 봉투 파싱 없이 바로 ApiError가 되고 null로 흡수된다", async () => {
    const fetchMock = stubFetch({ detail: "not found" }, 404);

    const result = await makeClient().getRiskProfile();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result).toBeNull();
  });

  it("getRiskProfileHistory: 비봉투 GET — URL이 이관 전과 동일하다", async () => {
    const fetchMock = stubFetch([
      { risk_profile: "안정형", assessed_at: "2026-01-01T00:00:00Z", answers: { a: 1 } },
    ]);

    const result = await makeClient().getRiskProfileHistory();

    expect(urlOf(fetchMock)).toBe("https://api.example.test/users/me/risk-profile/history");
    expect(result).toEqual([{ riskProfile: "안정형", assessedAt: "2026-01-01T00:00:00Z", answers: { a: 1 } }]);
  });

  it("getApprovalSettings: 봉투 GET — data를 언랩해 camelCase로 반환한다", async () => {
    const fetchMock = stubFetch(
      envelopeOk({ mode: "DUAL", second_approver_contact: "a@b.c", mandatory_wait_seconds: 60, risk_warning: null }),
    );

    const result = await makeClient().getApprovalSettings();

    expect(urlOf(fetchMock)).toBe("https://api.example.test/users/me/approval-settings");
    expect(result).toEqual({ mode: "DUAL", secondApproverContact: "a@b.c", mandatoryWaitSeconds: 60, riskWarning: null });
  });

  it("getApprovalSettings: negative — 200이어도 봉투 error_code면 ApiError를 던진다(requestEnvelope 경로 증명)", async () => {
    stubFetch(envelopeErr("SOME_ERROR"), 200);

    await expect(makeClient().getApprovalSettings()).rejects.toThrow(ApiError);
  });

  it("listWhitelistEntries / listMyApprovalRequests: 봉투 GET URL이 이관 전과 동일하다", async () => {
    const fetchMock = stubFetch(envelopeOk([{ id: 1, exchange: "upbit", destination_address: "addr", label: null }]));
    await makeClient().listWhitelistEntries();
    expect(urlOf(fetchMock)).toBe("https://api.example.test/users/me/withdrawal-whitelist");

    const fetchMock2 = stubFetch(envelopeOk([]));
    await makeClient().listMyApprovalRequests();
    expect(urlOf(fetchMock2)).toBe("https://api.example.test/users/me/approval-requests");
  });
});

describe("admin — requestByRoute(치환·쿼리 없음)/resolveEnvelope(치환·쿼리 있음) 배선", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("getVerificationQueue: 치환·쿼리 없는 봉투 GET은 requestByRoute를 그대로 쓴다", async () => {
    const fetchMock = stubFetch(envelopeOk([]));

    await makeClient().getVerificationQueue();

    expect(urlOf(fetchMock)).toBe("https://api.example.test/admin/verification-queue");
  });

  it("listAdminDisputes: 쿼리 파라미터를 유지한 채 봉투 분기는 레지스트리를 따른다", async () => {
    const fetchMock = stubFetch(envelopeOk([]));

    await makeClient().listAdminDisputes("OPEN");

    expect(urlOf(fetchMock)).toBe("https://api.example.test/admin/disputes?dispute_status=OPEN");
  });

  it("getAdminDispute: :disputeId 치환을 유지한 채 봉투를 언랩해 camelCase로 반환한다", async () => {
    const fetchMock = stubFetch(
      envelopeOk({
        dispute_id: 9,
        purchase_id: 1,
        submitted_by: "u1",
        reason: "r",
        status: "OPEN",
        listing_id: 2,
        listing_status: "ACTIVE",
        seller_user_id: "s1",
        buyer_user_id: "b1",
        created_at: "2026-01-01T00:00:00Z",
      }),
    );

    const result = await makeClient().getAdminDispute(9);

    expect(urlOf(fetchMock)).toBe("https://api.example.test/admin/disputes/9");
    expect(result.disputeId).toBe(9);
  });

  it("listAdminUsers: negative — 쿼리 경로도 200 + 봉투 error_code면 ApiError를 던진다", async () => {
    stubFetch(envelopeErr("X"), 200);

    await expect(makeClient().listAdminUsers("a@b.c")).rejects.toThrow(ApiError);
  });

  it("listPendingTopups / listPendingApprovalRequests: 쿼리 파라미터를 유지한다", async () => {
    const fetchMock = stubFetch(envelopeOk({ items: [], total: 0, page: 2, page_size: 10 }));
    await makeClient().listPendingTopups(2, 10);
    expect(urlOf(fetchMock)).toBe("https://api.example.test/admin/wallet/topups/pending?page=2&page_size=10");

    const fetchMock2 = stubFetch(envelopeOk([]));
    await makeClient().listPendingApprovalRequests("PLATFORM");
    expect(urlOf(fetchMock2)).toBe("https://api.example.test/admin/approval-requests/pending?scope=PLATFORM");
  });
});

describe("auth — requestByRoute 배선(봉투 1건)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("getMe: 봉투 GET — URL·파싱이 이관 전과 동일하다", async () => {
    const fetchMock = stubFetch(
      envelopeOk({
        user_id: "u1",
        email: "a@b.c",
        display_name: null,
        mfa_enabled: true,
        status: "ACTIVE",
        is_verifier: false,
        is_platform_admin: false,
      }),
    );

    const result = await makeClient().getMe();

    expect(urlOf(fetchMock)).toBe("https://api.example.test/users/me");
    expect(result).toEqual({
      userId: "u1",
      email: "a@b.c",
      displayName: null,
      mfaEnabled: true,
      status: "ACTIVE",
      isVerifier: false,
      isPlatformAdmin: false,
    });
  });

  it("getMe: negative — 200이어도 봉투 error_code면 ApiError를 던진다", async () => {
    stubFetch(envelopeErr("VALIDATION_ERROR"), 200);

    await expect(makeClient().getMe()).rejects.toThrow(ApiError);
  });
});

describe("exchange — requestByRoute(치환 없음)/resolveEnvelope(:exchange 치환) 배선", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("listExchangeCredentials: 치환 없는 비봉투 GET은 requestByRoute를 그대로 쓴다", async () => {
    const fetchMock = stubFetch([
      { id: 1, exchange: "upbit", is_active: true, linked_at: "2026-01-01T00:00:00Z", withdrawal_permission_warning: null },
    ]);

    const result = await makeClient().listExchangeCredentials();

    expect(urlOf(fetchMock)).toBe("https://api.example.test/exchange-credentials");
    expect(result).toEqual([
      { id: 1, exchange: "upbit", isActive: true, linkedAt: "2026-01-01T00:00:00Z", withdrawalPermissionWarning: null },
    ]);
  });

  it("getExchangeBalance: :exchange 치환을 유지한 채 비봉투 분기는 레지스트리를 따른다", async () => {
    const fetchMock = stubFetch([{ exchange: "upbit", asset: "KRW", total: "1000", available: "900" }]);

    const result = await makeClient().getExchangeBalance("upbit");

    expect(urlOf(fetchMock)).toBe("https://api.example.test/exchange-credentials/upbit/balance");
    expect(result).toEqual([{ exchange: "upbit", asset: "KRW", total: "1000", available: "900" }]);
  });

  it("getExchangeCapabilities: negative — 비봉투 GET은 404에서 봉투 파싱 없이 바로 ApiError를 던진다", async () => {
    const fetchMock = stubFetch({ detail: "not found" }, 404);

    await expect(makeClient().getExchangeCapabilities("upbit")).rejects.toThrow(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
