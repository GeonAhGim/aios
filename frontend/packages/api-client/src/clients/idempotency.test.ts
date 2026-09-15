import { afterEach, describe, expect, it, vi } from "vitest";
import { canonicalJson } from "@aios/shared-types";
import { AiosApiClient } from "../client";
import { checkDigest, createIdempotencyDigestStore } from "../idempotencyDigest";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): AiosApiClient {
  return new AiosApiClient("https://api.example.test", () => null);
}

function idempotencyKeyOf(fetchMock: ReturnType<typeof vi.fn>): string | null {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return new Headers(init.headers).get("Idempotency-Key");
}

// spec §9 PLT-14/15: 금전 POST는 Idempotency-Key(16~128자, [A-Za-z0-9_-]) 필수.
// http.ts의 postIdempotent/postEnvelopeIdempotent가 키를 자동 부착하므로
// 호출자가 명시적으로 넘기지 않아도 각 클라이언트 메서드가 헤더를 채운다.
describe("금전 라우트의 Idempotency-Key 자동 부착", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("purchaseListing: 키를 넘기지 않으면 UUID를 자동 생성해 헤더에 싣는다", async () => {
    const fetchMock = stubFetch({ purchase_id: 1, status: "PENDING", risk_warning: false, risk_warning_reason: null });

    await makeClient().purchaseListing(7, {});

    expect(idempotencyKeyOf(fetchMock)).toMatch(UUID_RE);
  });

  it("purchaseListing: 키를 넘기면 그대로 재사용한다(재시도 안전성)", async () => {
    const fetchMock = stubFetch({ purchase_id: 1, status: "PENDING", risk_warning: false, risk_warning_reason: null });

    await makeClient().purchaseListing(7, {}, "caller-supplied-key-0001");

    expect(idempotencyKeyOf(fetchMock)).toBe("caller-supplied-key-0001");
  });

  it("confirmTopup(봉투 적용 라우트): 키를 넘기지 않으면 자동 생성한다", async () => {
    const fetchMock = stubFetch({
      data: { topup_id: 1, status: "CONFIRMED" },
      meta: { trace_id: "t-1", as_of: "2026-09-03T00:00:00Z", page: null },
    });

    await makeClient().confirmTopup(1);

    expect(idempotencyKeyOf(fetchMock)).toMatch(UUID_RE);
  });

  it("createExecution: 키를 넘기지 않으면 자동 생성한다", async () => {
    const fetchMock = stubFetch({ id: 1, status: "PENDING" });

    await makeClient().createExecution({
      strategyId: "s-1",
      strategyVersion: "1.0.0",
      allocatedCapital: "100",
      currency: "USDT",
      exchange: "bitget",
      mode: "PAPER",
    });

    expect(idempotencyKeyOf(fetchMock)).toMatch(UUID_RE);
  });

  // startExecution/convertToLive/rebalancePortfolio/requestTopup: idempotencyKey가
  // 필수 인자다(누락 시 타입 에러) — 호출부(useIdempotentSubmit)가 넘긴 키를 그대로 싣는지만 확인.
  it("startExecution: 넘긴 키를 그대로 헤더에 싣는다", async () => {
    const startFetch = stubFetch({ id: 1, status: "RUNNING" });
    await makeClient().startExecution(1, "caller-supplied-key-0005");
    expect(idempotencyKeyOf(startFetch)).toBe("caller-supplied-key-0005");
  });

  it("convertToLive: 넘긴 키를 그대로 헤더에 싣는다", async () => {
    const convertFetch = stubFetch({ id: 1, status: "LIVE" });
    await makeClient().convertToLive(
      1,
      { allocatedCapital: "100", currency: "USDT", exchange: "bitget" },
      "caller-supplied-key-0002",
    );
    expect(idempotencyKeyOf(convertFetch)).toBe("caller-supplied-key-0002");
  });

  it("rebalancePortfolio: 넘긴 키를 그대로 헤더에 싣는다", async () => {
    const fetchMock = stubFetch({ adjusted: 1, pending_approval: 0, approval_request_ids: [] });

    await makeClient().rebalancePortfolio({ adjustments: [] }, "caller-supplied-key-0003");

    expect(idempotencyKeyOf(fetchMock)).toBe("caller-supplied-key-0003");
  });

  it("requestTopup: 넘긴 키를 그대로 헤더에 싣는다", async () => {
    const fetchMock = stubFetch({ id: 1, requested_amount: "30000", status: "PENDING" });

    await makeClient().requestTopup({ amount: "30000" }, "caller-supplied-key-0004");

    expect(idempotencyKeyOf(fetchMock)).toBe("caller-supplied-key-0004");
  });

  it("registerExchangeCredential: 키를 넘기지 않으면 자동 생성한다", async () => {
    const fetchMock = stubFetch({ exchange: "bitget", status: "ACTIVE" });

    await makeClient().registerExchangeCredential({ exchange: "bitget", apiKey: "k", apiSecret: "s" });

    expect(idempotencyKeyOf(fetchMock)).toMatch(UUID_RE);
  });

  it("자동 생성 시 호출마다 다른 키를 생성한다", async () => {
    const first = stubFetch({ purchase_id: 1, status: "PENDING", risk_warning: false, risk_warning_reason: null });
    await makeClient().purchaseListing(7, {});
    const firstKey = idempotencyKeyOf(first);

    const second = stubFetch({ purchase_id: 2, status: "PENDING", risk_warning: false, risk_warning_reason: null });
    await makeClient().purchaseListing(7, {});
    const secondKey = idempotencyKeyOf(second);

    expect(firstKey).not.toBe(secondKey);
  });
});

// DEPTH_PLT 감사(task-2730)가 원 task-338을 D1로 판정한 두 결함을 메운다:
// 수치 성능 단언(예산 있는 실측)과 게이트 적색 재현(회귀 시 실제로 실패하는
// 대조군). 매 금전 POST마다 postIdempotent/postEnvelopeIdempotent가 거치는
// guardIdempotentBody -> checkDigest -> canonicalJson(body) 경로(httpIdempotent.ts/
// idempotencyDigest.ts)를 그대로 재현한다.
describe("Idempotency-Key digest 선검증의 수치 성능(spec §9 PLT-15)", () => {
  function bigRebalanceBody(n: number): { adjustments: Array<{ symbol: string; targetWeight: string; reason: string }> } {
    return {
      adjustments: Array.from({ length: n }, (_, i) => ({
        symbol: `SYM-${i % 500}`,
        targetWeight: (i / n).toFixed(6),
        reason: `rebalance adjustment #${i} triggered by drift threshold breach`,
      })),
    };
  }

  // 제출 지연이 digest 계산으로 번지지 않음을 수치로 못박는다 — 예산 초과는
  // canonicalJson/checkDigest 어딘가가 O(n) 이상으로 퇴행했다는 신호다.
  it("rebalancePortfolio급 대량 adjustments(n=5000)도 150ms 예산 안에서 checkDigest를 끝낸다", async () => {
    const body = bigRebalanceBody(5000);
    const store = createIdempotencyDigestStore();

    const start = performance.now();
    const result = await checkDigest("clients/portfolio/rebalance:perf-budget-key", body, store);
    const elapsedMs = performance.now() - start;

    expect(result).toBe("new");
    expect(elapsedMs).toBeLessThan(150);
  });

  // 게이트 적색 재현: canonicalDigest.ts가 기대하는 구현(배열 map 1회, O(n))과
  // 달리, 원소를 추가할 때마다 누적 배열 전체를 다시 JSON.stringify하는 회귀
  // 구현(O(n^2))을 같은 입력으로 대조한다 — canonicalJson이 이 패턴으로
  // 퇴행하면 예산(150ms)을 넘겨 이 단언이 즉시 실패로 드러난다.
  function o2RegressionCanonicalJson(value: { adjustments: unknown[] }): string {
    let serialized = "";
    const accumulated: unknown[] = [];
    for (const item of value.adjustments) {
      accumulated.push(item);
      // 매 반복마다 지금까지 누적된 전체 배열을 다시 직렬화 -- 부분 결과를
      // 재사용하지 않는 회귀 패턴(O(n^2)).
      serialized = JSON.stringify(accumulated);
    }
    return serialized;
  }

  it("실제 canonicalJson은 예산 안에 끝나지만, O(n^2) 회귀 구현은 같은 입력에서 예산을 초과한다(게이트 적색)", () => {
    const body = bigRebalanceBody(5000);

    const realStart = performance.now();
    canonicalJson(body);
    const realElapsedMs = performance.now() - realStart;

    const regressionStart = performance.now();
    o2RegressionCanonicalJson(body);
    const regressionElapsedMs = performance.now() - regressionStart;

    expect(realElapsedMs).toBeLessThan(150);
    expect(regressionElapsedMs).toBeGreaterThan(150);
  });
});
