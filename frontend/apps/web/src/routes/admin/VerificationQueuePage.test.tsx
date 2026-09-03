import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { VerificationQueuePage } from "./VerificationQueuePage";

const verifyMutate = vi.fn();

const LISTING = {
  listingId: 7,
  strategyId: "strat-1",
  strategyVersion: "1",
  sellerUserId: "u1",
  price: "1000",
  submittedAt: "2026-09-01T00:00:00Z",
};

vi.mock("@aios/shared-hooks", () => ({
  useVerificationQueue: () => ({ data: [LISTING], isLoading: false }),
  useVerifyListing: () => ({ mutate: verifyMutate }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  verifyMutate.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <VerificationQueuePage />
    </MemoryRouter>,
  );
}

function clickApprove() {
  fireEvent.click(screen.getByRole("button", { name: "승인" }));
}

function clickReject() {
  fireEvent.click(screen.getByRole("button", { name: "반려" }));
}

// task-1156 §3.3: 지금까지 verify.mutate가 콜백 없이 호출돼 실패를 완전히
// 조용히 삼켰다 — 에러 상태 자체가 없었다. 이 화면에서 실제 가능한 코드
// (403/404/409)를 각각 ForbiddenNotice/ErrorMessage 경로로 표면화한다.
describe("VerificationQueuePage 검수 판정 실패 표시", () => {
  it("negative: POLICY_LIVE_BLOCKED(403) 승인 실패는 정책 거부 안내를 보여준다", async () => {
    verifyMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(403, "raw policy detail", "trace-1", "POLICY_LIVE_BLOCKED"),
      );
    });
    renderPage();

    clickApprove();

    await waitFor(() =>
      expect(
        screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw policy detail")).not.toBeInTheDocument();
  });

  it("negative: RESOURCE_NOT_FOUND(404) 반려 실패는 항목 없음 안내를 보여준다", async () => {
    verifyMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(404, "raw not found detail", "trace-2", "RESOURCE_NOT_FOUND"),
      );
    });
    renderPage();

    clickReject();

    await waitFor(() =>
      expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw not found detail")).not.toBeInTheDocument();
  });

  it("negative: STATE_INVALID_TRANSITION(409) 승인 실패는 err.message 대신 매핑 문구를 보여준다(이미 처리된 건)", async () => {
    verifyMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(409, "raw transition detail", "trace-3", "STATE_INVALID_TRANSITION"),
      );
    });
    renderPage();

    clickApprove();

    await waitFor(() =>
      expect(
        screen.getByText("현재 상태에서는 수행할 수 없는 작업입니다."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw transition detail")).not.toBeInTheDocument();
  });
});
