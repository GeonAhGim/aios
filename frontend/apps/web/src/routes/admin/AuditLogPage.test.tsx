import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { AuditLogPage } from "./AuditLogPage";
import { perfBudgetMs } from "../../test/perfBudget";

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
  refetchAuditLog.mockReset();
  useAuditLogSpy.mockReset();
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

// task-4025(FE-OPS-7b): admin.py:84 GET /admin/audit-log는 서버에 실재하지만(task-3850
// PLT-35-fix) 화면이 없어 ghost path였다. require_break_glass("tenant_read")를 소비하므로
// 그랜트 id 없이는 쿼리 자체를 실행하지 않는다(useAuditLog(task-4025)의 enabled 가드) --
// 이 화면에서 나올 수 있는 코드(403/409/500)를 각각 ForbiddenNotice/ErrorMessage로
// 표면화한다. 409(STATE_INVALID_TRANSITION)는 admin_deps.py::require_break_glass가
// 유효하지 않은/만료된/스코프 불일치 그랜트를 BreakGlassInvalidStateError로 던지고
// exception_registry.py가 그걸 STATE_INVALID_TRANSITION에 매핑하는 실경로다(방어적
// 커버가 아니다).
describe("AuditLogPage 그랜트 입력 전 상태", () => {
  it("negative: 그랜트 ID를 입력하기 전에는 쿼리를 실행하지 않고 안내만 보여준다", () => {
    renderPage();

    expect(screen.getByText("그랜트 ID를 입력하고 조회하세요.")).toBeInTheDocument();
    expect(useAuditLogSpy).toHaveBeenCalledWith("", expect.anything());
    expect(screen.queryByTestId("audit-log-list")).not.toBeInTheDocument();
  });

  it("그랜트 ID가 비어 있으면 조회 버튼이 비활성화된다", () => {
    renderPage();

    expect(screen.getByRole("button", { name: "조회" })).toBeDisabled();
  });
});

describe("AuditLogPage 조회 실패 표시", () => {
  it("negative: 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    forcedError = new ApiError(500, "일시적인 오류입니다.", "trace-audit-1");
    renderPage();

    search();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("감사 로그가 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: AUTHZ_FORBIDDEN(403)으로 실패하면 ForbiddenNotice 문구를 보여준다", async () => {
    forcedError = new ApiError(403, "raw forbidden detail", "trace-audit-2", "AUTHZ_FORBIDDEN");
    renderPage();

    search();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden detail")).not.toBeInTheDocument();
  });

  it("[failure injection] 유효하지 않은/만료된 그랜트(STATE_INVALID_TRANSITION, 409)는 err.message 대신 매핑 문구를 보여준다", async () => {
    forcedError = new ApiError(
      409,
      "grant_id=g-1: scope='ledger_write'는 'tenant_read' 작업에 쓸 수 없습니다.",
      "trace-audit-3",
      "STATE_INVALID_TRANSITION",
    );
    renderPage();

    search();

    await waitFor(() =>
      expect(screen.getByText("현재 상태에서는 수행할 수 없는 작업입니다.")).toBeInTheDocument(),
    );
    expect(
      screen.queryByText("grant_id=g-1: scope='ledger_write'는 'tenant_read' 작업에 쓸 수 없습니다."),
    ).not.toBeInTheDocument();
  });

  it("positive: 조회 결과가 실제로 비어 있으면(에러 없이 items=[]) 빈 상태를 보여준다", async () => {
    allEntries = [];
    renderPage();

    search();

    await waitFor(() => expect(screen.getByText("감사 로그가 없습니다.")).toBeInTheDocument());
  });
});

describe("AuditLogPage 조회 성공 표시", () => {
  it("조회 성공 시 항목이 렌더되고 필터 값이 쿼리에 전달된다", async () => {
    allEntries = [entry({ logId: 7, actionType: "MARK_PAYOUT_PAID", actorAgent: "admin@example.com", userId: "u-9" })];
    renderPage();

    fireEvent.change(screen.getByLabelText("액션 타입"), { target: { value: "MARK_PAYOUT_PAID" } });
    search();

    await waitFor(() => expect(screen.getByText("MARK_PAYOUT_PAID")).toBeInTheDocument());
    expect(useAuditLogSpy).toHaveBeenCalledWith(
      "11111111-1111-1111-1111-111111111111",
      expect.objectContaining({ actionType: "MARK_PAYOUT_PAID", page: 1, pageSize: 20 }),
    );
  });

  it(
    "[perf] 감사 로그 50건을 렌더링해도 예산 안에서 끝난다(항목별 O(1) 렌더 가드)",
    async () => {
      allEntries = Array.from({ length: 50 }, (_, i) => entry({ logId: i, targetId: `u-${i}` }));
      const startedAt = performance.now();
      renderPage();

      search();

      await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(20));
      const elapsedMs = performance.now() - startedAt;

      expect(elapsedMs).toBeLessThan(perfBudgetMs(4000));
    },
    20000,
  );
});
