import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { DisputeManagementPage } from "./DisputeManagementPage";

const mutate = vi.fn();

const DISPUTE = {
  id: 1,
  purchaseId: 10,
  submittedBy: "buyer-1",
  reason: "전략이 설명과 다릅니다.",
  status: "OPEN",
  resolutionDecision: null,
  resolutionReason: null,
  resolvedBy: null,
  createdAt: "2026-09-01T00:00:00Z",
  resolvedAt: null,
};

vi.mock("@aios/shared-hooks", () => ({
  useAdminDisputes: () => ({ data: [DISPUTE], isLoading: false }),
  useResolveDispute: () => ({ mutate }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutate.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <DisputeManagementPage />
    </MemoryRouter>,
  );
}

function clickResolve() {
  fireEvent.click(screen.getByRole("button", { name: "정상 리스크 실현(기각)" }));
}

// task-1072 §3.3: 지금까지 resolve.mutate가 콜백 없이 호출돼 실패를 완전히
// 조용히 삼켰다 — POLICY_*/RISK_* 403은 details.reason_codes 봉투를 ForbiddenNotice/
// DenialReasons로 보여주고, 그 외는 ErrorMessage로 안내해야 한다.
describe("DisputeManagementPage 처리(resolve) 실패 표시", () => {
  it("POLICY_*(403) + details.reason_codes 봉투: ForbiddenNotice가 사유 목록을 보여준다", async () => {
    mutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(403, "실거래 모드에서는 허용되지 않는 작업입니다.", "trace-1", "POLICY_LIVE_BLOCKED", undefined, {
          reason_codes: ["POLICY_LIVE_BLOCKED", "RISK_MAX_DRAWDOWN_EXCEEDED"],
        }),
      );
    });
    renderPage();

    clickResolve();

    // 분쟁 목록 자체도 <ul>이라 getByRole("list")는 모호해진다 — DenialReasons가 추가로
    // 렌더한 사유 목록(2번째 <ul>)을 집어 그 안의 문구를 단언한다. 배너 <p>와 DenialReasons
    // <li>가 POLICY_LIVE_BLOCKED에 같은 문구를 렌더하므로(ForbiddenNotice.test.tsx와 동일
    // 이유) 페이지 전체 getByText는 쓸 수 없다.
    await waitFor(() => expect(screen.getAllByRole("list")).toHaveLength(2));
    const denialList = screen.getAllByRole("list")[1];
    expect(denialList).toHaveTextContent("실거래 모드에서는 허용되지 않는 작업입니다.");
    expect(denialList).toHaveTextContent("최대 손실 한도를 초과하여 거부되었습니다.");
  });

  it("negative: AUTHZ_FORBIDDEN(403, reason_codes 없음)은 권한 없음 안내만 보여주고 사유 목록은 없다", async () => {
    mutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(403, "권한이 없습니다.", undefined, "AUTHZ_FORBIDDEN"));
    });
    renderPage();

    clickResolve();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    // 분쟁 목록 자체가 <ul>이므로(role list 1개) DenialReasons가 추가로 렌더되지
    // 않았다면 여전히 1개여야 한다.
    expect(screen.getAllByRole("list")).toHaveLength(1);
  });

  it("negative: INTERNAL_ERROR(500)는 err.message 대신 ErrorMessage의 매핑 문구·지원코드를 보여주고 재시도 불가(fatal)다", async () => {
    mutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(500, "raw internal detail", "trace-2", "INTERNAL_ERROR"));
    });
    renderPage();

    clickResolve();

    await waitFor(() =>
      expect(
        screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("지원코드: trace-2")).toBeInTheDocument();
    expect(screen.queryByText("raw internal detail")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });
});
