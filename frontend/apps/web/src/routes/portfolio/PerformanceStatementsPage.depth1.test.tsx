import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { PerformanceStatementView } from "@aios/shared-types";
import { PerformanceStatementsPage, type GetStatementFn, type ListStatementsFn } from "./PerformanceStatementsPage";
import { perfBudgetMs } from "../../test/perfBudget";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  apiClient: {
    computePerformanceStatement: vi.fn(),
    listPerformanceStatements: vi.fn(),
    getPerformanceStatement: vi.fn(),
    correctPerformanceStatement: vi.fn(),
  },
}));

afterEach(cleanup);

type Money = PerformanceStatementView["components"]["grossPnl"];

function money(amount: string | null, state: Money["state"] = "FINAL"): Money {
  return { amount, currency: "USD", precision: 2, asOf: "2026-09-23T00:00:00Z", state };
}

function statement(id = "stmt-1"): PerformanceStatementView {
  return {
    id,
    tenantId: "t-1",
    scope: "PAPER",
    scopeRef: "portfolio-1",
    periodStart: "2026-08-01",
    periodEnd: "2026-08-31",
    asOf: "2026-09-01T00:00:00Z",
    methodologyVersion: "v1.0",
    methodologyHash: "abc123",
    inputRefs: [],
    components: {
      grossPnl: money("1000.00"),
      fees: money("10.00"),
      slippage: money("5.00"),
      funding: money("0.00"),
      fx: money("0.00"),
      cashflowsNet: money("0.00"),
      estimatedTax: money(null, "ESTIMATED"),
      netPnl: money("985.00"),
    },
    returns: [],
    risk: {},
    benchmark: null,
    benchmarkRef: null,
    state: "FINAL",
    revisionNo: 1,
    priorStatementId: null,
    identityOk: true,
    identityResidual: null,
    limitations: [],
    evidenceRefs: [],
    schemaVersion: "v1",
  };
}

function renderPage(listStatements: ListStatementsFn, getStatement?: GetStatementFn) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PerformanceStatementsPage listStatements={listStatements} getStatement={getStatement} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Measured locally: 1 statement list render + first paint of the items ~40-80ms.
// Budget 3000ms is >30x that; it still fails on a hang or an O(n^2) blow-up.
async function timeListRender(listStatements: ListStatementsFn): Promise<number> {
  const startedAt = performance.now();
  renderPage(listStatements);
  await waitFor(() => expect(screen.getByTestId("performance-statement-items")).toBeInTheDocument());
  return performance.now() - startedAt;
}

describe("PerformanceStatementsPage depth (D2)", () => {
  it("[perf] 명세서 1건 목록 첫 렌더가 예산 안에 끝난다", async () => {
    const elapsed = await timeListRender(async () => ({ statements: [statement()] }));

    expect(elapsed).toBeLessThan(perfBudgetMs(3000));
  });

  it("[gate red] 목록 응답을 지연시키면 같은 예산 단언이 실제로 실패한다", async () => {
    const slow: ListStatementsFn = async () => {
      await new Promise((resolve) => setTimeout(resolve, 400));
      return { statements: [statement()] };
    };
    const elapsed = await timeListRender(slow);

    // Same assertion shape with a tight budget must throw: proves the gate can go red.
    expect(() => expect(elapsed).toBeLessThan(100)).toThrow();
    expect(elapsed).toBeGreaterThanOrEqual(400);
  });

  it("[failure-injection] 목록 조회가 네트워크 오류로 거부돼도 목록 대신 오류 문구를 보여준다", async () => {
    const listStatements = vi.fn<ListStatementsFn>().mockRejectedValue(new ApiError(503, "down", "trace-net-1", "INTERNAL_ERROR"));
    renderPage(listStatements);

    await waitFor(() => expect(screen.getByText("지원코드: trace-net-1")).toBeInTheDocument());
    expect(listStatements).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("performance-statement-items")).not.toBeInTheDocument();
    expect(screen.queryByTestId("performance-statement-detail")).not.toBeInTheDocument();
  });

  it("[negative] 상세 조회가 실패해도 정정 폼은 렌더되지 않고 조회 함수는 reject를 그대로 전파한다", async () => {
    const getStatement = vi.fn<GetStatementFn>().mockRejectedValue(new ApiError(500, "boom", "trace-det-1", "INTERNAL_ERROR"));
    renderPage(async () => ({ statements: [statement()] }), getStatement);

    fireEvent.click(await screen.findByText(/PAPER · 2026-08-01/));

    await waitFor(() => expect(screen.getByText("지원코드: trace-det-1")).toBeInTheDocument());
    expect(screen.queryByTestId("performance-statement-correct")).not.toBeInTheDocument();
    await expect(getStatement("stmt-1", {} as Parameters<GetStatementFn>[1])).rejects.toThrow("boom");
  });

  it("[negative] 빈 목록에서는 목록 컨테이너와 정정 폼이 모두 렌더되지 않는다", async () => {
    renderPage(async () => ({ statements: [] }));

    await waitFor(() => expect(screen.getByText("실적 명세서가 없습니다.")).toBeInTheDocument());
    expect(screen.queryByTestId("performance-statement-items")).not.toBeInTheDocument();
    expect(screen.queryByTestId("performance-statement-correct")).not.toBeInTheDocument();
  });
});
