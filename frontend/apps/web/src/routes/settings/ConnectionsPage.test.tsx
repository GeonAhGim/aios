import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { ConnectionsPage } from "./ConnectionsPage";

const beginConnectionMutate = vi.fn();
const confirmConnectionMutate = vi.fn();
const syncConnectionMutate = vi.fn();
const revokeConnectionMutate = vi.fn();
const refetchConnections = vi.fn();

const CONNECTION = {
  id: "connection-1",
  providerCode: "toss",
  maskedAccountLabel: "toss ****1234",
  state: "ACTIVE_READONLY",
  capabilityProfile: ["READ_BALANCE"],
  revision: 1,
  createdAt: "2026-09-08T00:00:00Z",
  scopeVerified: true,
  schemaVersion: "v1",
};

let connectionsResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = {
  data: { connections: [CONNECTION], asOf: "2026-09-09T00:00:00Z" },
  isLoading: false,
  isError: false,
  error: null,
  refetch: refetchConnections,
};

vi.mock("@aios/shared-hooks", () => ({
  useConnections: () => connectionsResult,
  useBeginConnection: () => ({ mutate: beginConnectionMutate, isPending: false }),
  useConfirmConnection: () => ({ mutate: confirmConnectionMutate, isPending: false }),
  useSyncConnection: () => ({ mutate: syncConnectionMutate, isPending: false }),
  useRevokeConnection: () => ({ mutate: revokeConnectionMutate, isPending: false }),
  useMe: () => ({ data: { email: "user@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  beginConnectionMutate.mockReset();
  confirmConnectionMutate.mockReset();
  syncConnectionMutate.mockReset();
  revokeConnectionMutate.mockReset();
  refetchConnections.mockReset();
  connectionsResult = {
    data: { connections: [CONNECTION], asOf: "2026-09-09T00:00:00Z" },
    isLoading: false,
    isError: false,
    error: null,
    refetch: refetchConnections,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ConnectionsPage />
    </MemoryRouter>,
  );
}

describe("ConnectionsPage 목록 조회 실패/빈 상태 표시", () => {
  it("negative: 목록 조회가 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    connectionsResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-cx-1"),
      refetch: refetchConnections,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("등록된 계정 연동이 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 목록 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice 문구를 보여준다", async () => {
    connectionsResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw forbidden detail", "trace-cx-2", "AUTHZ_FORBIDDEN"),
      refetch: refetchConnections,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument());
    expect(screen.queryByText("raw forbidden detail")).not.toBeInTheDocument();
  });

  it("positive: 연동 목록이 실제로 비어 있으면(에러 없이 connections=[]) 빈 상태를 보여준다", async () => {
    connectionsResult = {
      data: { connections: [], asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchConnections,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("등록된 계정 연동이 없습니다.")).toBeInTheDocument());
  });
});

describe("ConnectionsPage 생성(begin)", () => {
  it("제공자 코드·참조값·권한 범위를 입력하고 생성을 누르면 beginConnection을 호출한다", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("예: toss"), { target: { value: "kb" } });
    fireEvent.change(screen.getByPlaceholderText("연동 대상 참조값"), { target: { value: "ref-1" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "READ_BALANCE" }));
    fireEvent.click(screen.getByRole("button", { name: "생성" }));

    expect(beginConnectionMutate).toHaveBeenCalledWith(
      { providerCode: "kb", opaqueAccountRef: "ref-1", requestedCapabilityProfile: ["READ_BALANCE"] },
      expect.anything(),
    );
  });

  it("필수 입력이 없으면 생성 버튼이 비활성화된다", () => {
    renderPage();

    expect(screen.getByRole("button", { name: "생성" })).toBeDisabled();
  });
});

describe("ConnectionsPage 행별 액션(confirm/sync/revoke)", () => {
  it("확인(confirm) 버튼을 누르면 connectionId로 mutate를 호출한다", () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "확인(confirm)" }));

    expect(confirmConnectionMutate).toHaveBeenCalledWith("connection-1", expect.anything());
  });

  it("동기화(sync) 성공 시 스냅샷 값을 행에 표시한다", async () => {
    syncConnectionMutate.mockImplementation((_id, opts) => {
      opts?.onSuccess?.({
        connectionId: "connection-1",
        capturedAt: "2026-09-09T01:00:00Z",
        providerAsOf: "2026-09-09T00:55:00Z",
        freshness: "FRESH",
        currency: "KRW",
        values: [{ entityType: "ACCOUNT", entityKey: "cash", value: "1000000" }],
        schemaVersion: "v1",
      });
      opts?.onSettled?.();
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "동기화(sync)" }));

    await waitFor(() => expect(screen.getByText(/ACCOUNT\/cash: 1000000/)).toBeInTheDocument());
  });

  it("negative: 해제(revoke)가 교차 테넌트 404(RESOURCE_NOT_FOUND)로 실패하면 빈 상태가 아니라 명시적 오류로 보여준다", async () => {
    revokeConnectionMutate.mockImplementation((_id, opts) => {
      opts?.onError?.(new ApiError(404, "raw not found detail", "trace-cx-3", "RESOURCE_NOT_FOUND"));
      opts?.onSettled?.();
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "해제(revoke)" }));

    await waitFor(() => expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument());
    expect(screen.queryByText("raw not found detail")).not.toBeInTheDocument();
  });
});
