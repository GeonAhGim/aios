import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { ReconciliationPage } from "./ReconciliationPage";

const resolveMutate = vi.fn();
const refetchStates = vi.fn();

const STATE = {
  targetRef: "acct-1",
  targetType: "ACCOUNT",
  aggregateStatus: "MATERIAL_MISMATCH",
  lastHealthyAt: "2026-09-08T00:00:00Z",
  lastCheckedAt: "2026-09-09T00:00:00Z",
  blockingReason: "USDT_BALANCE 불일치",
  revision: 3,
  schemaVersion: "v1",
};

let statesResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = {
  data: { states: [STATE], asOf: "2026-09-09T00:00:00Z" },
  isLoading: false,
  isError: false,
  error: null,
  refetch: refetchStates,
};

vi.mock("@aios/shared-hooks", () => ({
  useReconciliationStates: () => statesResult,
  useResolveReconciliation: () => ({ mutate: resolveMutate, isPending: false }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  resolveMutate.mockReset();
  refetchStates.mockReset();
  statesResult = {
    data: { states: [STATE], asOf: "2026-09-09T00:00:00Z" },
    isLoading: false,
    isError: false,
    error: null,
    refetch: refetchStates,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ReconciliationPage />
    </MemoryRouter>,
  );
}

function clickResolve() {
  fireEvent.click(screen.getByRole("button", { name: "불일치 해소" }));
}

describe("ReconciliationPage 목록 조회 실패/빈 상태 표시", () => {
  it("negative: 목록 조회가 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    statesResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-rc-1"),
      refetch: refetchStates,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("대사 상태가 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 목록 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice 문구를 보여준다", async () => {
    statesResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw forbidden list detail", "trace-rc-2", "AUTHZ_FORBIDDEN"),
      refetch: refetchStates,
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden list detail")).not.toBeInTheDocument();
  });

  it("positive: 대사 상태가 실제로 비어 있으면(에러 없이 states=[]) 빈 상태를 보여준다", async () => {
    statesResult = {
      data: { states: [], asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchStates,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("대사 상태가 없습니다.")).toBeInTheDocument());
  });

  it("해소 대상이 아닌 상태(HEALTHY)는 해소 버튼/사유 입력을 보여주지 않는다", () => {
    statesResult = {
      data: { states: [{ ...STATE, aggregateStatus: "HEALTHY", blockingReason: null }], asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchStates,
    };
    renderPage();

    expect(screen.queryByRole("button", { name: "불일치 해소" })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("해소 사유")).not.toBeInTheDocument();
  });
});

describe("ReconciliationPage 불일치 해소(resolve)", () => {
  it("해소 사유 없이 클릭하면 서버 왕복 없이 입력 오류를 보여준다", () => {
    renderPage();

    clickResolve();

    expect(screen.getByText("해소 사유를 입력하세요.")).toBeInTheDocument();
    expect(resolveMutate).not.toHaveBeenCalled();
  });

  it("해소 사유를 입력하면 targetRef·reason으로 mutate를 호출한다", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("해소 사유"), { target: { value: "확인 완료, 오탐" } });
    clickResolve();

    expect(resolveMutate).toHaveBeenCalledWith(
      { targetRef: "acct-1", body: { reason: "확인 완료, 오탐" } },
      expect.anything(),
    );
  });

  it("negative: AUTHZ_FORBIDDEN(403) 해소 실패는 권한 없음 안내를 보여준다", async () => {
    resolveMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(403, "raw forbidden detail", "trace-3", "AUTHZ_FORBIDDEN"));
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("해소 사유"), { target: { value: "재확인" } });
    clickResolve();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden detail")).not.toBeInTheDocument();
  });

  it("negative: STATE_INVALID_TRANSITION(409, resolve 대상 아님)은 err.message 대신 매핑 문구를 보여준다", async () => {
    resolveMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(409, "MATERIAL_MISMATCH 상태는 resolve 대상이 아닙니다.", "trace-4", "STATE_INVALID_TRANSITION"),
      );
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("해소 사유"), { target: { value: "재확인" } });
    clickResolve();

    await waitFor(() =>
      expect(screen.getByText("현재 상태에서는 수행할 수 없는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("MATERIAL_MISMATCH 상태는 resolve 대상이 아닙니다.")).not.toBeInTheDocument();
  });
});
