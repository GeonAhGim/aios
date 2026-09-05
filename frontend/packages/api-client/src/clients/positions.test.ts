import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
import { ApiError } from "../httpErrors";
import { createPositionsClient } from "./positions";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Trace-Id": "trace-1" },
  });
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestUrl(fetchMock: ReturnType<typeof vi.fn>): string {
  const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
  return url;
}

function makeClient() {
  return createPositionsClient("https://api.example.test", () => "token");
}

const META = { trace_id: "trace-1", as_of: "2026-09-05T12:00:00Z", page: null };

const SNAPSHOT = {
  position_key: "upbit:BTC-KRW:strat-1:exec-1",
  tenant_id: "11111111-1111-4111-8111-111111111111",
  account_id: "22222222-2222-4222-8222-222222222222",
  instrument_id: "33333333-3333-4333-8333-333333333333",
  quantity: "1.5",
  avg_cost: { amount: "50000.00", currency: "KRW" },
  cost_method: "FIFO",
  lots: [{ quantity: "1.5", unit_cost: "50000.00", opened_at: "2026-09-01T00:00:00Z", schema_version: "v1" }],
  realized_pnl_base: "1000.00",
  unrealized_pnl_base: null,
  fees_base: "10.00",
  funding_base: "0.00",
  mark_price: null,
  mark_at: null,
  base_currency: "KRW",
  last_journal_seq: 3,
  updated_at: "2026-09-03T00:00:00Z",
  schema_version: "v1",
};

const NAV = {
  schema_version: "v1",
  account_id: SNAPSHOT.account_id,
  nav_date: "2026-09-04",
  base_currency: "KRW",
  opening_nav: "100000.00",
  cash: "5000.00",
  positions_mv: "96000.00",
  realized: "1000.00",
  unrealized_delta: "500.00",
  funding: "0.00",
  fees: "10.00",
  flows: "0.00",
  closing_nav: "101000.00",
  fx_rates: [],
  source_hash: "abc",
};

// LB-19(src/api/routers/positions.py) 세 GET을 apiPaths.ts 레지스트리 경로로만 호출하고,
// 봉투 data는 positionView.ts 파서로 판별하며, 저널 커서는 meta.page.next_cursor를
// 문자열 그대로 왕복시키는지 고정한다. 에러는 ApiError로 그대로 올린다(재분류 없음).
describe("createPositionsClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("경로·봉투 여부는 apiPaths.ts 레지스트리에만 정의되어 있다(positions.py prefix /v1/positions)", () => {
    expect(API_ROUTES["positions.list"]).toMatchObject({ legacyPath: "/v1/positions", envelope: true });
    expect(API_ROUTES["positions.nav"]).toMatchObject({ legacyPath: "/v1/positions/nav", envelope: true });
    expect(API_ROUTES["positions.journal"]).toMatchObject({
      legacyPath: "/v1/positions/:positionKey/journal",
      envelope: true,
    });
  });

  it("listPositions: 봉투 data.items를 parsePositionSnapshot으로 항목마다 판별하고 meta.as_of를 돌려준다", async () => {
    const fetchMock = stubFetch({ data: { items: [SNAPSHOT, { schema_version: "v2" }] }, meta: META });

    const result = await makeClient().listPositions({ accountId: SNAPSHOT.account_id });

    expect(requestUrl(fetchMock)).toBe(
      `https://api.example.test/v1/positions?account_id=${SNAPSHOT.account_id}`,
    );
    expect(result.asOf).toBe("2026-09-05T12:00:00Z");
    expect(result.items).toHaveLength(2);
    expect(result.items[0].kind).toBe("ok");
    if (result.items[0].kind === "ok") {
      // mark 없음(POS_MARK_STALE): null을 "0"으로 뭉개지 않고 그대로 보존한다.
      expect(result.items[0].value.unrealized_pnl_base).toBeNull();
      expect(result.items[0].value.mark_price).toBeNull();
      expect(result.items[0].value.lots[0].unit_cost).toBe("50000.00");
    }
    expect(result.items[1]).toEqual({ kind: "unsupported_schema_version", received: "v2" });
  });

  it("listPositions: 필터 없이 호출하면 쿼리스트링을 붙이지 않는다", async () => {
    const fetchMock = stubFetch({ data: { items: [] }, meta: META });

    const result = await makeClient().listPositions();

    expect(requestUrl(fetchMock)).toBe("https://api.example.test/v1/positions");
    expect(result.items).toEqual([]);
  });

  it("getPositionJournal: position_key를 인코딩해 경로에 넣고 cursor 문자열을 그대로 실어 보낸다(숫자 변환 금지)", async () => {
    const fetchMock = stubFetch({
      data: { position_key: SNAPSHOT.position_key, items: [{ sequence_no: 4, entry_type: "FILL", schema_version: "v1" }] },
      meta: { ...META, page: { total: null, page: null, size: 50, next_cursor: "0004" } },
    });

    const result = await makeClient().getPositionJournal({
      positionKey: SNAPSHOT.position_key,
      cursor: "0003",
      limit: 50,
    });

    expect(requestUrl(fetchMock)).toBe(
      "https://api.example.test/v1/positions/upbit%3ABTC-KRW%3Astrat-1%3Aexec-1/journal?cursor=0003&limit=50",
    );
    // meta.page.next_cursor를 받은 그대로(선행 0 포함) 돌려준다 — Number()를 거치면 "4"가 돼 계약이 깨진다.
    expect(result.nextCursor).toBe("0004");
    expect(result.positionKey).toBe(SNAPSHOT.position_key);
    expect(result.items).toEqual([{ sequence_no: 4, entry_type: "FILL", schema_version: "v1" }]);
  });

  it("getPositionJournal: 마지막 페이지(next_cursor=null)면 nextCursor가 null이고 첫 페이지 요청엔 cursor를 붙이지 않는다", async () => {
    const fetchMock = stubFetch({
      data: { position_key: "k", items: [] },
      meta: { ...META, page: { total: null, page: null, size: 50, next_cursor: null } },
    });

    const result = await makeClient().getPositionJournal({ positionKey: "k" });

    expect(requestUrl(fetchMock)).toBe("https://api.example.test/v1/positions/k/journal");
    expect(result.nextCursor).toBeNull();
  });

  it("getNavSeries: account_id·start_date·end_date를 쿼리로 보내고 items는 parseNavSnapshot, missing_dates는 그대로 돌려준다", async () => {
    const fetchMock = stubFetch({
      data: {
        account_id: NAV.account_id,
        start_date: "2026-09-03",
        end_date: "2026-09-05",
        items: [NAV],
        missing_dates: ["2026-09-03", "2026-09-05"],
      },
      meta: META,
    });

    const result = await makeClient().getNavSeries({
      accountId: NAV.account_id,
      startDate: "2026-09-03",
      endDate: "2026-09-05",
    });

    expect(requestUrl(fetchMock)).toBe(
      `https://api.example.test/v1/positions/nav?account_id=${NAV.account_id}&start_date=2026-09-03&end_date=2026-09-05`,
    );
    expect(result.items).toHaveLength(1);
    expect(result.items[0].kind).toBe("ok");
    if (result.items[0].kind === "ok") expect(result.items[0].value.closing_nav).toBe("101000.00");
    // 빠진 날은 0으로 채우지 않고 사실 그대로 노출한다(FD-3.3).
    expect(result.missingDates).toEqual(["2026-09-03", "2026-09-05"]);
    expect(result.asOf).toBe(META.as_of);
  });

  it("negative: 404 RESOURCE_NOT_FOUND 봉투는 ApiError(404, errorCode)로 그대로 던진다(재분류·빈 목록 대체 금지)", async () => {
    stubFetch(
      { error_code: "RESOURCE_NOT_FOUND", message: "없음", details: {}, trace_id: "trace-1", retry_after_seconds: null },
      404,
    );

    await expect(makeClient().getPositionJournal({ positionKey: "ghost" })).rejects.toMatchObject({
      statusCode: 404,
      errorCode: "RESOURCE_NOT_FOUND",
      traceId: "trace-1",
    });
  });

  it("negative: 403 TENANT_MISMATCH 봉투도 ApiError로 그대로 던진다", async () => {
    stubFetch(
      { error_code: "TENANT_MISMATCH", message: "forbidden", details: {}, trace_id: "trace-1", retry_after_seconds: null },
      403,
    );

    const err = await makeClient().listPositions().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).statusCode).toBe(403);
  });

  it("negative: 봉투가 아닌 몸체(data/meta 없음)는 EnvelopeFormatError로 실패한다(조용히 빈 목록으로 뭉개지 않음)", async () => {
    stubFetch({ items: [SNAPSHOT] });

    await expect(makeClient().listPositions()).rejects.toThrow(/봉투 형식/);
  });
});
