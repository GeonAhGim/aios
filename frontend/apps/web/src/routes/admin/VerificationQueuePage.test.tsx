import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { VerificationQueuePage } from "./VerificationQueuePage";

const verifyMutate = vi.fn();
const refetchQueue = vi.fn();

const LISTING = {
  listingId: 7,
  strategyId: "strat-1",
  strategyVersion: "1",
  sellerUserId: "u1",
  price: "1000",
  submittedAt: "2026-09-01T00:00:00Z",
};

let queueResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = {
  data: [LISTING],
  isLoading: false,
  isError: false,
  error: null,
  refetch: refetchQueue,
};

vi.mock("@aios/shared-hooks", () => ({
  useVerificationQueue: () => queueResult,
  useVerifyListing: () => ({ mutate: verifyMutate }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  verifyMutate.mockReset();
  refetchQueue.mockReset();
  queueResult = {
    data: [LISTING],
    isLoading: false,
    isError: false,
    error: null,
    refetch: refetchQueue,
  };
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
describe("VerificationQueuePage 목록 조회 실패/빈 상태 표시", () => {
  it("negative: 대기열 조회가 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    queueResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-queue-1"),
      refetch: refetchQueue,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("대기 중인 검수 건이 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 대기열 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice 문구를 보여준다", async () => {
    queueResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw forbidden list detail", "trace-queue-2", "AUTHZ_FORBIDDEN"),
      refetch: refetchQueue,
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden list detail")).not.toBeInTheDocument();
    expect(screen.queryByText("대기 중인 검수 건이 없습니다.")).not.toBeInTheDocument();
  });

  it("positive: 대기열이 실제로 비어 있으면(에러 없이 data=[]) 빈 상태를 보여준다", async () => {
    queueResult = { data: [], isLoading: false, isError: false, error: null, refetch: refetchQueue };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("대기 중인 검수 건이 없습니다.")).toBeInTheDocument(),
    );
  });
});

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

// useVerifyListing은 성공 시 실제로 ["verificationQueue"] 쿼리를 invalidate해
// 대기열을 재조회한다(packages/shared-hooks/src/useMarketplace.ts) — 여기서는 그
// 재조회 결과가 실제로 반영되면 처리된 건이 목록에서 사라지는지를 확인한다.
describe("VerificationQueuePage 판정 성공 시 목록 갱신", () => {
  it("승인 성공 후 재조회 결과가 반영되면 대기열에서 사라지고 빈 상태가 표시된다", async () => {
    const { rerender } = renderPage();

    clickApprove();

    queueResult = {
      data: [],
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchQueue,
    };
    rerender(
      <MemoryRouter>
        <VerificationQueuePage />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByText("대기 중인 검수 건이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("strat-1@1")).not.toBeInTheDocument();
  });
});
