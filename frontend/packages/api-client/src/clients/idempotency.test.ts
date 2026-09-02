import { afterEach, describe, expect, it, vi } from "vitest";
import { AiosApiClient } from "../client";

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

  it("startExecution: 키를 넘기지 않으면 자동 생성한다", async () => {
    const startFetch = stubFetch({ id: 1, status: "RUNNING" });
    await makeClient().startExecution(1);
    expect(idempotencyKeyOf(startFetch)).toMatch(UUID_RE);
  });

  // convertToLive/rebalancePortfolio/requestTopup: task-338부터 idempotencyKey가
  // 필수 인자다(누락 시 타입 에러) — 호출부(useIdempotentSubmit)가 넘긴 키를 그대로 싣는지만 확인.
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
