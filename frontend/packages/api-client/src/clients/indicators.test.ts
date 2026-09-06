import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
import { createIndicatorsClient } from "./indicators";

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  return fetchMock.mock.calls[0] as [string, RequestInit];
}

function makeClient() {
  return createIndicatorsClient("https://api.example.test", () => null);
}

const ITEM = {
  name: "SMA",
  tier: "core",
  category: "overlap",
  version: "ind-v1",
  hash: "sha256:abc",
  inputs: ["close"],
  outputs: ["value"],
};

describe("indicators apiPaths 레지스트리: envelope=true, v1Path 미배선", () => {
  it("indicators.list", () => {
    expect(API_ROUTES["indicators.list"].envelope).toBe(true);
    expect(API_ROUTES["indicators.list"].v1Path).toBeUndefined();
    expect(API_ROUTES["indicators.list"].legacyPath).toBe("/v1/indicators");
  });
});

describe("createIndicatorsClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listIndicators: GET /v1/indicators, query가 snake_case로 나가고 응답을 camelCase 항목으로 파싱한다", async () => {
    const fetchMock = stubFetch({
      data: { items: [ITEM] },
      meta: { trace_id: "t-1", as_of: "2026-09-07T00:00:00Z", page: { size: 50, next_cursor: "SMA" } },
    });

    const result = await makeClient().listIndicators({ q: "SM", category: "overlap", cursor: "R", limit: 50 });

    const [url, init] = requestOf(fetchMock);
    expect(url).toBe(
      "https://api.example.test/v1/indicators?q=SM&category=overlap&cursor=R&limit=50",
    );
    expect(init.method ?? "GET").toBe("GET");
    expect(result).toEqual({
      items: [
        { name: "SMA", tier: "core", category: "overlap", version: "ind-v1", hash: "sha256:abc", inputs: ["close"], outputs: ["value"] },
      ],
      nextCursor: "SMA",
    });
  });

  it("listIndicators: 파라미터 없이도 동작하고 마지막 페이지면 nextCursor가 null이다", async () => {
    const fetchMock = stubFetch({ data: { items: [] }, meta: { trace_id: "t-1", as_of: "2026-09-07T00:00:00Z", page: { size: 50, next_cursor: null } } });

    const result = await makeClient().listIndicators();

    const [url] = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/indicators");
    expect(result).toEqual({ items: [], nextCursor: null });
  });

  it("항목 필드가 계약과 다르면(미지 필드 폴백 금지) throw한다", async () => {
    stubFetch({
      data: { items: [{ ...ITEM, tier: "unknown-tier" }] },
      meta: { trace_id: "t-1", as_of: "2026-09-07T00:00:00Z", page: null },
    });
    await expect(makeClient().listIndicators()).rejects.toThrow(/tier/);
  });
});
