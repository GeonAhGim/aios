import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES, resolvePath } from "../apiPaths";
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

// task-4024(FE-OPS-7a): admin.py:84 GET /admin/audit-log · ledger_admin.py:53 POST
// /admin/ledger/payouts/{batch_id}/paid — 둘 다 require_break_glass("tenant_read")가
// 요구하는 X-Break-Glass-Grant 헤더가 배선의 핵심이다(나머지 admin.* 메서드는 이
// 헤더가 필요 없었다). D2 하한(negative ≥3·실패 주입 1·수치 성능 단언 1·게이트
// 적색 재현 1)을 foundation.test.ts의 DEEPEN 3185 블록과 동일한 관용으로 채운다.
describe("admin — audit-log/ledger payout(task-4024 FE-OPS-7a) X-Break-Glass-Grant 배선", () => {
  afterEach(() => vi.unstubAllGlobals());

  function breakGlassGrantHeader(init: RequestInit): string | null {
    return new Headers(init.headers).get("X-Break-Glass-Grant");
  }

  const payoutBatchBody = {
    batch_id: "b1",
    seller_user_id: "s1",
    period_start: "2026-08-01",
    period_end: "2026-08-31",
    amount: "1000.00",
    state: "PAID",
    capture_entry_ids: ["e1"],
    release_entry_id: "e2",
    paid_entry_id: "e3",
  };

  it("listAuditLog: 필터 쿼리 + X-Break-Glass-Grant 헤더를 실어 봉투 GET을 호출하고 camelCase로 언랩한다", async () => {
    const fetchMock = stubFetch(
      envelopeOk({
        items: [
          {
            log_id: 1,
            user_id: "u1",
            actor_agent: "admin-console",
            action_type: "PAYOUT_MARK_PAID",
            target_type: "payout_batch",
            target_id: "b1",
            decision_data: { note: "ok" },
            verification_chain: null,
            created_at: "2026-09-17T00:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        page_size: 50,
      }),
    );

    const result = await makeClient().listAuditLog("11111111-1111-1111-1111-111111111111", {
      actionType: "PAYOUT_MARK_PAID",
      page: 1,
      pageSize: 50,
    });

    expect(urlOf(fetchMock)).toBe(
      "https://api.example.test/admin/audit-log?action_type=PAYOUT_MARK_PAID&page=1&page_size=50",
    );
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(breakGlassGrantHeader(init)).toBe("11111111-1111-1111-1111-111111111111");
    expect(result.items[0]).toEqual({
      logId: 1,
      userId: "u1",
      actorAgent: "admin-console",
      actionType: "PAYOUT_MARK_PAID",
      targetType: "payout_batch",
      targetId: "b1",
      decisionData: { note: "ok" },
      verificationChain: null,
      createdAt: "2026-09-17T00:00:00Z",
    });
  });

  it("listAuditLog: negative — 200이어도 봉투 error_code면 ApiError를 던진다", async () => {
    stubFetch(envelopeErr("SOME_ERROR"), 200);

    await expect(makeClient().listAuditLog("11111111-1111-1111-1111-111111111111")).rejects.toThrow(ApiError);
  });

  it("listAuditLog: negative(실패 주입) — 만료·오용된 grant는 403 AUTH_MFA_REQUIRED로 거부되고 그대로 전파된다(require_break_glass 소비 실패를 흉내)", async () => {
    stubFetch(
      { error_code: "AUTH_MFA_REQUIRED", message: "grant invalid", details: {}, trace_id: "t1", retry_after_seconds: null },
      403,
    );

    await expect(makeClient().listAuditLog("expired-grant-id")).rejects.toThrow(ApiError);
  });

  it("markPayoutPaid: :batchId 치환 + externalRef를 external_ref로 스네이크케이싱하고 헤더를 싣는다", async () => {
    const fetchMock = stubFetch(envelopeOk(payoutBatchBody));

    const result = await makeClient().markPayoutPaid(
      "b1",
      "wire-2026-09-17",
      "22222222-2222-2222-2222-222222222222",
    );

    expect(urlOf(fetchMock)).toBe("https://api.example.test/admin/ledger/payouts/b1/paid");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ external_ref: "wire-2026-09-17" });
    expect(breakGlassGrantHeader(init)).toBe("22222222-2222-2222-2222-222222222222");
    expect(result).toEqual({
      batchId: "b1",
      sellerUserId: "s1",
      periodStart: "2026-08-01",
      periodEnd: "2026-08-31",
      amount: "1000.00",
      state: "PAID",
      captureEntryIds: ["e1"],
      releaseEntryId: "e2",
      paidEntryId: "e3",
    });
  });

  it("markPayoutPaid: negative(실패 주입) — 이미 PAID인 배치 재확정은 409 CONCURRENCY_CONFLICT로 거부된다(조건부 UPDATE 실패를 흉내)", async () => {
    stubFetch(
      { error_code: "CONCURRENCY_CONFLICT", message: "already paid", details: {}, trace_id: "t1", retry_after_seconds: null },
      409,
    );

    await expect(
      makeClient().markPayoutPaid("b1", "wire-2026-09-17", "22222222-2222-2222-2222-222222222222"),
    ).rejects.toThrow(ApiError);
  });

  // 게이트 적색 재현: 이 리프 전까지 admin.ts의 모든 메서드는 추가 헤더가 필요
  // 없었다 — postEnvelope의 3번째 인자(extraHeaders, task-4024)를 빠뜨리는 실수가
  // 나기 쉬운 지점이다. 버그 버전(헤더 미부착)과 실제 구현(부착)을 같은 라우트에
  // 나란히 돌려 차이를 직접 증명한다.
  class BuggyAdminClient extends ApiClientBase {
    async markPayoutPaidMissingGrantHeader(batchId: string, externalRef: string): Promise<unknown> {
      return this.postEnvelope(resolvePath("admin.ledger.payoutsMarkPaid").replace(":batchId", batchId), {
        externalRef,
      });
    }
  }

  it("게이트 적색 재현: X-Break-Glass-Grant를 빠뜨리면(적색) 헤더가 비고, 실제 구현(녹색)은 채워 보낸다", async () => {
    const fetchMock1 = stubFetch(envelopeOk(payoutBatchBody));
    const buggyClient = new BuggyAdminClient("https://api.example.test", () => null);
    await buggyClient.markPayoutPaidMissingGrantHeader("b1", "wire-1");
    expect(breakGlassGrantHeader(fetchMock1.mock.calls[0][1] as RequestInit)).toBeNull();

    const fetchMock2 = stubFetch(envelopeOk(payoutBatchBody));
    await makeClient().markPayoutPaid("b1", "wire-1", "22222222-2222-2222-2222-222222222222");
    expect(breakGlassGrantHeader(fetchMock2.mock.calls[0][1] as RequestInit)).toBe(
      "22222222-2222-2222-2222-222222222222",
    );
  });

  // 수치 성능: body 직렬화(keysToSnake)·헤더 조립이 반복 호출에서 선형 이하로
  // 끝나는지 실측한다 — mock fetch는 매 호출마다 새 Response를 즉시 resolve하므로
  // (stubFetch는 Response 인스턴스 하나를 재사용해 두 번째 호출부터 body를 다시
  // 읽으려다 실패한다 — mockImplementation으로 매번 새로 만든다) 이 시간은 거의
  // 전부 클라이언트 쪽 직렬화 비용이다.
  it("수치 성능: markPayoutPaid 200회 연속 호출이 200ms 이내에 끝난다", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => jsonResponse(200, envelopeOk(payoutBatchBody)));
    vi.stubGlobal("fetch", fetchMock);
    const client = makeClient();

    const start = performance.now();
    for (let i = 0; i < 200; i++) {
      await client.markPayoutPaid("b1", `wire-${i}`, "22222222-2222-2222-2222-222222222222");
    }
    const elapsedMs = performance.now() - start;

    expect(fetchMock).toHaveBeenCalledTimes(200);
    expect(elapsedMs).toBeLessThan(200);
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
