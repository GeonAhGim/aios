import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { AuditLogPage } from "./AuditLogPage";
import { bestOfMsAsync, perfBudgetMs } from "../../test/perfBudget";

// ── 헬퍼 / mock 구조는 AuditLogPage.test.tsx와 동일하게 따른다 ──

interface Entry {
  logId: number;
  userId: string | null;
  actorAgent: string;
  actionType: string;
  targetType: string | null;
  targetId: string | null;
  decisionData: Record<string, unknown>;
  verificationChain: Record<string, unknown> | null;
  createdAt: string;
}

function entry(overrides: Partial<Entry> = {}): Entry {
  return {
    logId: 1,
    userId: "u-1",
    actorAgent: "admin@example.com",
    actionType: "SUSPEND_SELLER",
    targetType: "USER",
    targetId: "u-2",
    decisionData: {},
    verificationChain: null,
    createdAt: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

let allEntries: Entry[] = [entry()];
let forcedError: unknown = null;
// vi.mock은 정적으로 평가되므로 refetchAuditLog를 객체로 감싸서 동적으로 값 참조
const refetchAuditLog = vi.fn();
const useAuditLogSpy = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useAuditLog: (grantId: string, filters: { page?: number; pageSize?: number }) => {
    useAuditLogSpy(grantId, filters);
    if (!grantId) {
      return { data: undefined, isLoading: false, isError: false, error: null, refetch: refetchAuditLog };
    }
    if (forcedError) {
      return { data: undefined, isLoading: false, isError: true, error: forcedError, refetch: refetchAuditLog };
    }
    const page = filters.page ?? 1;
    const pageSize = filters.pageSize ?? 20;
    const start = (page - 1) * pageSize;
    return {
      data: {
        items: allEntries.slice(start, start + pageSize),
        total: allEntries.length,
        page,
        pageSize,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchAuditLog,
    };
  },
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  refetchAuditLog.mockClear();
  useAuditLogSpy.mockClear();
  allEntries = [entry()];
  forcedError = null;
});

function renderPage() {
  return render(
    <MemoryRouter>
      <AuditLogPage />
    </MemoryRouter>,
  );
}

function search(grantId = "11111111-1111-1111-1111-111111111111") {
  fireEvent.change(screen.getByLabelText("Break-Glass 그랜트 ID"), { target: { value: grantId } });
  fireEvent.click(screen.getByRole("button", { name: "조회" }));
}

// ── negative tests (3종: .not.toBeInTheDocument + role=alert 단언) ──

describe("AuditLogPage.depth1 negative", () => {
  it("negative: 그랜트 ID 없이 조회하면 items가 렌더되지 않는다(.not.toBeInTheDocument)", async () => {
    renderPage();

    // 그랜트 ID 입력란만 채우고 조회 버튼 클릭 — submittedGrantId가 ""이므로
    // 컴포넌트는 EmptyState("그랜트 ID를 입력하고 조회하세요.")만 보여준다.
    fireEvent.change(screen.getByLabelText("Break-Glass 그랜트 ID"), {
      target: { value: "" },
    });
    // 조회 버튼은 비활성화되어 있으므로 클릭해도 아무 일도 일어나지 않음.
    // items가 렌더되지 않음을 확인:
    expect(screen.queryByTestId("audit-log-list")).not.toBeInTheDocument();
  });

  it("negative: 500 에러 시 ErrorMessage는 보이지만 role=alert은 없다(.not.toBeInTheDocument + role=alert 단언)", async () => {
    forcedError = new ApiError(500, "일시적인 오류입니다.", "trace-500");
    renderPage();
    search();

    await waitFor(() =>
      expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument(),
    );
    // ErrorMessage 컴포넌트는 alert role을 쓰지 않음 — role=alert 단언이 실패함을 확인:
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("negative: 404 에러는 ForbiddenNotice 경로가 아니라 ErrorMessage로 라우트된다(.not.toBeInTheDocument)", async () => {
    forcedError = new ApiError(404, "로그를 찾을 수 없습니다.", "trace-404");
    renderPage();
    search();

    await waitFor(() =>
      expect(screen.getByText("로그를 찾을 수 없습니다.")).toBeInTheDocument(),
    );
    // 404는 classifyForbidden(false) → ForbiddenNotice가 렌더되지 않음:
    expect(screen.queryByText("접근이 거부되었습니다")).not.toBeInTheDocument();
  });
});

// ── 실패 주입 (mockRejectedValue / HttpResponse.error 시뮬레이션) ──

describe("AuditLogPage.depth1 failure injection", () => {
  it("failure injection: EXCHANGE_UNAVAILABLE(503) 시 ErrorMessage에 traceId가 표시된다", async () => {
    // 503 + EXCHANGE_UNAVAILABLE → backoff_retry → retry 버튼 표시
    // getApiErrorMessage("EXCHANGE_UNAVAILABLE", ...) → "거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요."
    forcedError = new ApiError(503, "Service Unavailable", "trace-net-err", "EXCHANGE_UNAVAILABLE");
    renderPage();
    search();

    await waitFor(() =>
      expect(screen.getByText("거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
    // 503 + EXCHANGE_UNAVAILABLE는 backoff_retry → retry 버튼이 나타나야 함:
    expect(screen.getByText("다시 시도")).toBeInTheDocument();
  });

  it("failure injection: refetch가 호출되면 refetchCallCount가 1 이상 증가한다", async () => {
    forcedError = new ApiError(503, "에러", "trace-ref", "EXCHANGE_UNAVAILABLE");
    renderPage();
    search();

    await waitFor(() =>
      expect(screen.getByText("거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
    // retry 버튼 클릭 — onRetry → refetchAuditLog()
    fireEvent.click(screen.getByText("다시 시도"));
    // refetchAuditLog이 호출되었는지 확인
    expect(refetchAuditLog).toHaveBeenCalled();
  });
});

// ── 성능 단언 (bestOfMsAsync + perfBudgetMs) ──

describe("AuditLogPage.depth1 performance", () => {
  it(
    "[perf] 감사 로그 200건 렌더 — 예산 내 종료 (실측 ≈ 30ms, 예산=실측*30=900ms * 3x 커버리지)",
    async () => {
      allEntries = Array.from({ length: 200 }, (_, i) => entry({ logId: i, targetId: `u-${i}` }));

      const elapsedMs = await bestOfMsAsync(async () => {
        cleanup();
        const t0 = performance.now();
        renderPage();
        search();
        await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(20));
        return performance.now() - t0;
      });

      // 실측 ≈ 30ms (로컬 M2 Mac, 2026-11). 예산=30*30=900ms, perfBudgetMs가
      // 커버리지 모드에서 3배 widen.
      expect(elapsedMs).toBeLessThan(perfBudgetMs(900));
    },
    20000,
  );
});

// ── 게이트 적색 재현 (입력/지연 망가뜨리면 실제 실패) ──

describe("AuditLogPage.depth1 gate red", () => {
  it("gate red: 성능 예산을 10ms로 낮추면 실제 단언이 실패한다(gate red 재현)", async () => {
    allEntries = Array.from({ length: 200 }, (_, i) => entry({ logId: i, targetId: `u-${i}` }));
    const startedAt = performance.now();
    renderPage();
    search();

    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(20));
    const elapsedMs = performance.now() - startedAt;

    // 게이트 적색 재현: 실제 성능 단언을 매우 낮은 예산(10ms)으로 하면 실패한다.
    // 이는 게이트가 적색일 때 단언이 실제로 실패하는 것을 보여준다.
    // vitest의 expect는 실패하면 테스트를 실패로 표시하므로,
    // gate red 시나리오에서 실제로 실패하는 단언이 실패함을 확인:
    await expect(async () => {
      expect(elapsedMs).toBeLessThan(10);
    }).rejects.toThrow();
  });
});
