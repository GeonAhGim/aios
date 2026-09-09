import type { Page, Route } from "@playwright/test";

// H-7b 스모크 3건이 공유하는 고정 픽스처 서버. 실 백엔드 대신 baseUrl
// (apps/web 기본값 http://localhost:8000, clientInstance.ts 등)로 가는 모든
// 요청을 page.route로 가로챈다 — ProtectedRoute(useMe/useRiskProfile)를
// 통과시키는 최소 계약까지 포함해야 executions/chart 화면에 도달한다.
const API_BASE = "http://localhost:8000";
const TOKEN_STORAGE_KEY = "aios_access_token";

interface EnvelopeMeta {
  page?: { total: number | null; page: number | null; size: number; next_cursor: string | null } | null;
}

function envelope(data: unknown, meta: EnvelopeMeta = {}): unknown {
  return {
    data,
    meta: { trace_id: "e2e-trace", as_of: new Date().toISOString(), page: null, ...meta },
  };
}

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export interface MockIndicator {
  name: string;
  tier: "core" | "oss" | "script";
  category: string;
}

export interface MockResponseOverride {
  status: number;
  body: unknown;
}

export interface MockBackendOptions {
  /** GET /v1/indicators 카탈로그. 기본값은 지표 오버레이 스모크가 쓰는 최소 2종. */
  indicators?: MockIndicator[];
  /** POST /executions 응답을 통째로 바꾼다 — 실패 주입(변조 케이스) 테스트용. */
  createExecutionResponse?: MockResponseOverride;
  /** POST /v1/backtests/quick 응답을 통째로 바꾼다. */
  quickBacktestResponse?: MockResponseOverride;
}

function buildCandleSeries(instrumentId: string, venue: string, timeframe: string) {
  const stepMs = 60 * 60 * 1000;
  const now = Date.now();
  const candles = Array.from({ length: 24 }, (_, i) => {
    const openMs = now - (24 - i) * stepMs;
    return {
      key: { venue, instrument_id: instrumentId, timeframe },
      open_time: new Date(openMs).toISOString(),
      close_time: new Date(openMs + stepMs - 1).toISOString(),
      open: "100",
      high: "110",
      low: "90",
      close: "105",
      volume: "1000",
      quote_volume: null,
    };
  });
  return {
    schema_version: "v1",
    key: { venue, instrument_id: instrumentId, timeframe },
    candles,
    gaps: [],
    adjustment: "RAW",
    as_of: new Date(now).toISOString(),
    series_hash: "e2e-fixture-hash",
  };
}

/** 실행 목록에 쓰이는 서버측 상태. 성공 케이스에서 POST 후 목록에 반영되는지까지 본다. */
export interface MockBackendState {
  executions: Record<string, unknown>[];
}

export async function mockBackend(page: Page, options: MockBackendOptions = {}): Promise<MockBackendState> {
  const state: MockBackendState = { executions: [] };
  const indicators: MockIndicator[] = options.indicators ?? [
    { name: "sma", tier: "core", category: "trend" },
    { name: "rsi", tier: "core", category: "momentum" },
  ];

  await page.addInitScript(
    (key) => window.localStorage.setItem(key, "e2e-fixture-token"),
    TOKEN_STORAGE_KEY,
  );

  await page.route(`${API_BASE}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const { pathname } = url;

    if (pathname === "/users/me" && method === "GET") {
      return json(
        route,
        200,
        envelope({
          user_id: "e2e-user",
          email: "e2e@example.com",
          display_name: "E2E Fixture",
          mfa_enabled: true,
          status: "active",
          is_verifier: false,
          is_platform_admin: false,
        }),
      );
    }

    if (pathname === "/users/me/risk-profile" && method === "GET") {
      return json(route, 200, {
        risk_profile: "중립형",
        assessed_at: "2026-01-01T00:00:00Z",
        next_reassessment_due: "2099-01-01T00:00:00Z",
        is_higher_risk_than_previous: false,
      });
    }

    if (pathname === "/executions" && method === "GET") {
      return json(route, 200, state.executions);
    }

    if (pathname === "/executions" && method === "POST") {
      if (options.createExecutionResponse) {
        return json(route, options.createExecutionResponse.status, options.createExecutionResponse.body);
      }
      const body = (request.postDataJSON() ?? {}) as Record<string, unknown>;
      const executionId = state.executions.length + 1;
      state.executions.push({
        execution_id: executionId,
        strategy_id: body.strategy_id,
        strategy_version: body.strategy_version,
        status: "PENDING",
        mode: body.mode,
        exchange: body.exchange,
        allocated_capital: body.allocated_capital,
        days_since_start: null,
        realized_pnl: "0",
        unrealized_pnl: "0",
        max_drawdown_pct: null,
      });
      return json(route, 201, {
        id: executionId,
        status: "PENDING",
        mode: body.mode,
        exchange: body.exchange,
        allocated_capital: body.allocated_capital,
        approval_request_id: null,
        max_drawdown_pct: null,
      });
    }

    if (pathname === "/v1/indicators" && method === "GET") {
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      const items = indicators
        .filter((item) => !q || item.name.toLowerCase().includes(q))
        .map((item) => ({
          name: item.name,
          tier: item.tier,
          category: item.category,
          version: "1.0.0",
          hash: "e2e-hash",
          inputs: ["close"],
          outputs: [item.name],
        }));
      return json(
        route,
        200,
        envelope({ items }, { page: { total: items.length, page: 1, size: items.length, next_cursor: null } }),
      );
    }

    if (pathname === "/v1/foundation/market-data/candles" && method === "GET") {
      const venue = url.searchParams.get("venue") ?? "BITGET";
      const instrumentId = url.searchParams.get("instrument_id") ?? "BTCUSDT";
      const timeframe = url.searchParams.get("timeframe") ?? "1h";
      return json(route, 200, envelope(buildCandleSeries(instrumentId, venue, timeframe)));
    }

    if (pathname === "/v1/foundation/market-data/coverage" && method === "GET") {
      return json(route, 200, envelope([]));
    }

    if (pathname === "/v1/foundation/charting/layouts" && method === "GET") {
      return json(route, 200, envelope([]));
    }

    if (pathname === "/v1/backtests/quick" && method === "POST") {
      if (options.quickBacktestResponse) {
        return json(route, options.quickBacktestResponse.status, options.quickBacktestResponse.body);
      }
      return json(
        route,
        200,
        envelope({
          fills: [],
          equity_curve: ["10000", "10500"],
          final_equity: "10500",
          cash: "10500",
          position_quantity: "0",
          funding_cost: "0",
          borrow_cost: "0",
          bars: 24,
          expired_orders: 0,
          warnings: [],
        }),
      );
    }

    return route.fallback();
  });

  return state;
}
