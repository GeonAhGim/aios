import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { PerformanceStatementView } from "@aios/shared-types";
import {
  PerformanceStatementsPage,
  type ComputeStatementFn,
  type CorrectStatementFn,
  type GetStatementFn,
  type ListStatementsFn,
} from "./PerformanceStatementsPage";
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

function moneyValue(amount: string | null, overrides: Partial<PerformanceStatementView["components"]["grossPnl"]> = {}): PerformanceStatementView["components"]["grossPnl"] {
  return { amount, currency: "USD", precision: 2, asOf: "2026-09-23T00:00:00Z", state: "FINAL", ...overrides };
}

function statement(overrides: Partial<PerformanceStatementView> = {}): PerformanceStatementView {
  return {
    id: "stmt-1",
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
      grossPnl: moneyValue("1000.00"),
      fees: moneyValue("10.00"),
      slippage: moneyValue("5.00"),
      funding: moneyValue("0.00"),
      fx: moneyValue("0.00"),
      cashflowsNet: moneyValue("0.00"),
      estimatedTax: moneyValue(null, { state: "ESTIMATED" }),
      netPnl: moneyValue("985.00"),
    },
    returns: [
      { valuePct: "12.5", basis: "NET", method: "TWR", periodStart: "2026-08-01", periodEnd: "2026-08-31", annualized: false, periodsPerYear: null },
    ],
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
    ...overrides,
  };
}

function renderPage(opts: {
  computeStatement?: ComputeStatementFn;
  listStatements?: ListStatementsFn;
  getStatement?: GetStatementFn;
  correctStatement?: CorrectStatementFn;
} = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PerformanceStatementsPage
          computeStatement={opts.computeStatement}
          listStatements={opts.listStatements ?? (async () => ({ statements: [] }))}
          getStatement={opts.getStatement}
          correctStatement={opts.correctStatement}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PerformanceStatementsPage", () => {
  it("목록·상세를 정상 표시하고 선택한 명세서의 구성 요소를 렌더링한다", async () => {
    renderPage({
      listStatements: async () => ({ statements: [statement()] }),
      getStatement: async () => statement(),
    });

    await waitFor(() => expect(screen.getByTestId("performance-statement-items")).toBeInTheDocument());
    fireEvent.click(screen.getByText(/PAPER · 2026-08-01/));

    await waitFor(() => expect(screen.getByTestId("performance-statement-detail")).toBeInTheDocument());
    expect(screen.getByText("985.00 USD")).toBeInTheDocument();
  });

  it("amount:null(PENDING) 항목은 0으로 대체하지 않고 대시로 표시한다", async () => {
    renderPage({
      listStatements: async () => ({ statements: [statement()] }),
      getStatement: async () => statement(),
    });

    fireEvent.click(await screen.findByText(/PAPER · 2026-08-01/));

    await waitFor(() => expect(screen.getByTestId("performance-statement-detail")).toBeInTheDocument());
    expect(screen.getByText(/— USD/)).toBeInTheDocument();
  });

  it("[negative] 목록이 비어 있으면 빈 상태 안내만 보여주고 상세 카드는 렌더링하지 않는다", async () => {
    renderPage({ listStatements: async () => ({ statements: [] }) });

    await waitFor(() => expect(screen.getByText("실적 명세서가 없습니다.")).toBeInTheDocument());
    expect(screen.queryByTestId("performance-statement-detail")).not.toBeInTheDocument();
  });

  it("[negative] 목록 조회가 ApiError 봉투로 실패하면 매핑된 메시지와 지원코드를 보여준다", async () => {
    renderPage({
      listStatements: async () => {
        throw new ApiError(500, "server message", "trace-list-1", "INTERNAL_ERROR");
      },
    });

    await waitFor(() =>
      expect(screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument(),
    );
    expect(screen.getByText("지원코드: trace-list-1")).toBeInTheDocument();
  });

  it("[negative] 상세 조회가 404(RESOURCE_NOT_FOUND)면 재시도 배너가 아니라 '없음' 상태를 보여준다", async () => {
    renderPage({
      listStatements: async () => ({ statements: [statement({ id: "stmt-missing" })] }),
      getStatement: async () => {
        throw new ApiError(404, "not found", "trace-404-1", "RESOURCE_NOT_FOUND");
      },
    });

    fireEvent.click(await screen.findByText(/PAPER · 2026-08-01/));

    await waitFor(() => expect(screen.getByText("명세서를 찾을 수 없습니다")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("[negative] 정정 사유를 입력하지 않으면 정정 요청 버튼이 비활성화된다", async () => {
    renderPage({
      listStatements: async () => ({ statements: [statement()] }),
      getStatement: async () => statement(),
    });

    fireEvent.click(await screen.findByText(/PAPER · 2026-08-01/));
    await waitFor(() => expect(screen.getByTestId("performance-statement-correct")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "정정 요청" })).toBeDisabled();
  });

  it("scope=LIVE 계산 요청이 서버 422(UnsupportedStatementScopeError)로 거부되면 판정을 재구현하지 않고 그대로 보여준다", async () => {
    const computeStatement: ComputeStatementFn = async () => {
      throw new ApiError(422, "scope not supported", "trace-live-422", "VALIDATION_INVALID_FIELD");
    };
    renderPage({ computeStatement, listStatements: async () => ({ statements: [] }) });

    const form = screen.getByTestId("performance-statement-compute");
    fireEvent.change(within(form).getByLabelText("스코프"), { target: { value: "LIVE" } });
    fireEvent.change(within(form).getByLabelText("기간 시작"), { target: { value: "2026-08-01" } });
    fireEvent.change(within(form).getByLabelText("기간 종료"), { target: { value: "2026-08-31" } });
    fireEvent.click(within(form).getByRole("button", { name: "계산 요청" }));

    await waitFor(() => expect(screen.getByText("입력값을 확인해주세요.")).toBeInTheDocument());
  });

  it("[failure-injection] 정정 요청이 이미 정정된 명세서(상태 충돌)에 다시 제출되면 재시도 없이 상태 오류를 보여준다", async () => {
    const correctStatement: CorrectStatementFn = async () => {
      throw new ApiError(409, "already corrected", "trace-conflict-1", "STATE_INVALID_TRANSITION");
    };
    renderPage({
      listStatements: async () => ({ statements: [statement()] }),
      getStatement: async () => statement(),
      correctStatement,
    });

    fireEvent.click(await screen.findByText(/PAPER · 2026-08-01/));
    const form = await screen.findByTestId("performance-statement-correct");
    fireEvent.change(within(form).getByLabelText("정정 사유"), { target: { value: "가격 오류 정정" } });
    fireEvent.click(within(form).getByRole("button", { name: "정정 요청" }));

    await waitFor(() =>
      expect(screen.getByText("현재 상태에서는 수행할 수 없는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("정정 요청이 성공하면 성공 안내를 보여준다", async () => {
    const correctStatement: CorrectStatementFn = async () => statement({ revisionNo: 2, state: "CORRECTED" });
    renderPage({
      listStatements: async () => ({ statements: [statement()] }),
      getStatement: async () => statement(),
      correctStatement,
    });

    fireEvent.click(await screen.findByText(/PAPER · 2026-08-01/));
    const form = await screen.findByTestId("performance-statement-correct");
    fireEvent.change(within(form).getByLabelText("정정 사유"), { target: { value: "가격 오류 정정" } });
    fireEvent.click(within(form).getByRole("button", { name: "정정 요청" }));

    await waitFor(() => expect(screen.getByText("정정 요청이 반영되었습니다.")).toBeInTheDocument());
  });

  it(
    "[perf] 명세서 60건을 목록에 렌더링해도 예산 안에서 끝난다(항목별 O(1) 렌더 가드)",
    async () => {
      const items = Array.from({ length: 60 }, (_, i) =>
        statement({ id: `stmt-${i}`, periodStart: `2026-0${(i % 9) + 1}-01` }),
      );
      const startedAt = performance.now();
      renderPage({ listStatements: async () => ({ statements: items }) });

      await waitFor(() => expect(screen.getAllByRole("button").length).toBeGreaterThanOrEqual(60));
      const elapsedMs = performance.now() - startedAt;

      expect(elapsedMs).toBeLessThan(perfBudgetMs(8000));
    },
    20000,
  );
});

// ADR-2026-09-09-C D2 증거: negative 5건(빈 목록·500·404·정정 사유 미입력·이 파일
// 위쪽 amount:null), failure-injection 1건(상태 충돌 409 정정 재제출), perf
// assertion 1건(60건 렌더 예산). gate-red repro는 N/A(이 리프가 새 정적 게이트를
// 만들지 않음 — check_i18n_literals.mjs는 기존 스캐너로 이 파일도 이미 커버).
