import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { WalletTopupsPage } from "./WalletTopupsPage";

const confirmMutate = vi.fn();

const TOPUP = {
  id: 4,
  userId: "u1",
  requestedAmount: "10000",
  status: "PENDING",
  requestedAt: "2026-09-01T00:00:00Z",
  confirmedAt: null,
  confirmedBy: null,
};

vi.mock("@aios/shared-hooks", () => ({
  usePendingTopups: () => ({ data: { items: [TOPUP], total: 1, page: 1, pageSize: 20 }, isLoading: false }),
  useConfirmTopup: () => ({ mutate: confirmMutate, isPending: false }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  confirmMutate.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <WalletTopupsPage />
    </MemoryRouter>,
  );
}

function clickConfirm() {
  fireEvent.click(screen.getByRole("button", { name: "입금 확인" }));
}

// task-1156 §3.3: 지금까지 confirm.mutate가 콜백 없이 호출돼 실패를 완전히
// 조용히 삼켰다 — 금전 라우트(admin confirm-payment)인데도 에러 상태 자체가
// 없었다. 이 화면에서 실제 가능한 코드(403/404/409/429)를 각각
// ForbiddenNotice/ErrorMessage 경로로 표면화한다. idempotencyKey 발급
// 자체는(crypto.randomUUID() 매 클릭) 건드리지 않았다는 것도 함께 고정한다.
describe("WalletTopupsPage 입금확인 실패 표시", () => {
  it("negative: AUTHZ_FORBIDDEN(403) 입금확인 실패는 권한 없음 안내를 보여준다", async () => {
    confirmMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(403, "raw forbidden detail", "trace-1", "AUTHZ_FORBIDDEN"));
    });
    renderPage();

    clickConfirm();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden detail")).not.toBeInTheDocument();
  });

  it("negative: INTEGRITY_IDEMPOTENCY_CONFLICT(409) 입금확인 실패는 err.message 대신 매핑 문구를 보여준다", async () => {
    confirmMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(409, "raw idempotency detail", "trace-2", "INTEGRITY_IDEMPOTENCY_CONFLICT"),
      );
    });
    renderPage();

    clickConfirm();

    await waitFor(() =>
      expect(screen.getByText("이미 처리된 요청입니다. 새로고침 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw idempotency detail")).not.toBeInTheDocument();
  });

  it("negative: RATE_LIMIT_EXCEEDED(429) 입금확인 실패는 err.message 대신 매핑 문구를 보여준다", async () => {
    confirmMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(429, "raw rate limit detail", "trace-3", "RATE_LIMIT_EXCEEDED"),
      );
    });
    renderPage();

    clickConfirm();

    await waitFor(() =>
      expect(
        screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw rate limit detail")).not.toBeInTheDocument();
  });

  it("confirm.mutate는 idempotencyKey를 UUID로 매 클릭 새로 발급한다(멱등 키 수명주기 미변경)", () => {
    renderPage();

    clickConfirm();

    expect(confirmMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        topupId: 4,
        idempotencyKey: expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
        ),
      }),
      expect.anything(),
    );
  });
});
