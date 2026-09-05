import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
import { ApiClientBase } from "../http";
import { ApiError } from "../httpErrors";
import { withStrategyBuilder } from "./strategyBuilder";

class StrategyBuilderTestClient extends withStrategyBuilder(ApiClientBase) {}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): StrategyBuilderTestClient {
  return new StrategyBuilderTestClient("https://api.example.test", () => null);
}

function urlOf(fetchMock: ReturnType<typeof vi.fn>): string {
  return (fetchMock.mock.calls[0] as [string, RequestInit])[0];
}

// task-1145 QA: task-1106은 marketplace.* 7건만 envelope=false로 고정했고,
// strategyBuilder.* 8건은 apiPaths.openapi.test.ts의 스냅샷 대조로만 간접 보호됐다.
// src/api/routers/strategy_builder.py는 HEAD 기준 `ok()`/`ApiResponse`를 전혀
// 쓰지 않으므로(PLT-18은 raw HTTPException 제거만 완료, 봉투화는 mount_v1 배선
// 대기) 레지스트리가 true로 뒤집히면 requestEnvelope가 봉투 없는 응답을 파싱
// 실패시킨다 — 그 회귀를 라우트명으로 직접 고정한다(marketplace.test.ts와 동일 패턴).
describe("strategyBuilder apiPaths 레지스트리: 봉투 미적용 상태를 고정한다", () => {
  it.each([
    "strategyBuilder.indicators.list",
    "strategyBuilder.strategies.base",
    "strategyBuilder.candles",
    "strategyBuilder.indicators.compute",
    "strategyBuilder.strategies.get",
    "strategyBuilder.preview",
    "strategyBuilder.wizard",
    "strategyBuilder.generateFromPrompt",
  ] as const)("%s: envelope=false", (routeName) => {
    expect(API_ROUTES[routeName].envelope).toBe(false);
  });
});

describe("withStrategyBuilder", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listIndicators: requestByRoute로 레지스트리 경로를 그대로 쓴다", async () => {
    const fetchMock = stubFetch({ indicators: ["RSI", "MACD"] });

    const result = await makeClient().listIndicators();

    expect(urlOf(fetchMock)).toBe("https://api.example.test/strategy-builder/indicators");
    expect(result.indicators).toEqual(["RSI", "MACD"]);
  });

  it("computeIndicator: :name 치환 + 쿼리(undefined 생략) + 응답 camelCase", async () => {
    const fetchMock = stubFetch({ name: "RSI", values: [1, 2], registry_version: "v3" });

    const result = await makeClient().computeIndicator("RSI", { exchange: "bitget", symbol: "BTCUSDT", period: 14 });

    expect(urlOf(fetchMock)).toBe(
      "https://api.example.test/strategy-builder/indicators/RSI/compute?exchange=bitget&symbol=BTCUSDT&period=14",
    );
    expect((result as unknown as { registryVersion: string }).registryVersion).toBe("v3");
  });

  it("getStrategy: :strategyId/:version 두 자리 모두 치환한다", async () => {
    const fetchMock = stubFetch({ strategy_id: "s1", version: "v2", name: "n" });

    await makeClient().getStrategy("s1", "v2");

    expect(urlOf(fetchMock)).toBe("https://api.example.test/strategy-builder/strategies/s1/v2");
  });

  it("getStrategy 404(StrategyNotFoundError→RESOURCE_NOT_FOUND): ApiError로 거부되고 GET이라도 재시도하지 않는다", async () => {
    const fetchMock = stubFetch(
      { error_code: "RESOURCE_NOT_FOUND", message: "전략이 없습니다.", details: {}, trace_id: "t-404" },
      404,
    );

    const err = await makeClient()
      .getStrategy("missing", "v1")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(404);
    expect(err.errorCode).toBe("RESOURCE_NOT_FOUND");
    expect(err.traceId).toBe("t-404");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
