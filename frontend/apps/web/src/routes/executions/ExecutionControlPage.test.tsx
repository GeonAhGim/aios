import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { ExecutionControlPage } from "./ExecutionControlPage";

const mutateAsync = vi.fn();
let executionsData: unknown[] = [];

vi.mock("@aios/shared-hooks", () => ({
  useExecutions: () => ({ data: executionsData, isLoading: false }),
  useCreateExecution: () => ({ mutateAsync, isPending: false, data: undefined }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
  executionsData = [];
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ExecutionControlPage />
    </MemoryRouter>,
  );
}

function submitCreateForm(container: HTMLElement) {
  const strategyIdInput = container.querySelector("input") as HTMLInputElement;
  fireEvent.change(strategyIdInput, { target: { value: "strat-1" } });
  fireEvent.click(screen.getByRole("button", { name: "실행 생성" }));
}

// task-901 §3.3: 실행 생성 실패는 err.message를 직접 노출하지 않고 routeApiError로
// 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만 보여준다.
describe("ExecutionControlPage 실행 생성 에러 표시", () => {
  it("negative: POLICY_*(403) 거부는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-1", "POLICY_LIVE_BLOCKED"),
    );
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() => expect(screen.getByText("실행 생성에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  it("VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400)는 BadRequestNotice 경로로 새로고침 안내를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "raw detail", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"),
    );
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() =>
      expect(
        screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });
});
