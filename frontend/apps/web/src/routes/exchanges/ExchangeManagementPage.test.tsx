import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { ExchangeManagementPage } from "./ExchangeManagementPage";

const mutateAsync = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useExchangeCredentials: () => ({ data: [], isLoading: false }),
  useRegisterExchangeCredential: () => ({ mutateAsync, isPending: false }),
  useRevokeExchangeCredential: () => ({ mutate: vi.fn() }),
  useExchangeBalance: () => ({ data: undefined }),
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ExchangeManagementPage />
    </MemoryRouter>,
  );
}

// Field가 htmlFor 없이 label만 렌더링하므로(기존 화면 상태, 이 QA 범위 밖)
// getByLabelText 대신 input[type] 순서로 값을 채운다.
function submitRegisterForm(container: HTMLElement) {
  const [apiKey, apiSecret, apiPassphrase] = [
    container.querySelector('input[type="text"]'),
    ...container.querySelectorAll('input[type="password"]'),
  ] as HTMLInputElement[];
  fireEvent.change(apiKey, { target: { value: "key-1" } });
  fireEvent.change(apiSecret, { target: { value: "secret-1" } });
  fireEvent.change(apiPassphrase, { target: { value: "pass-1" } });
  fireEvent.click(screen.getByRole("button", { name: "등록" }));
}

// task-322 후속 결함: errorCode가 ErrorMessage에 전달되지 않아 EXCHANGE_FATAL 등
// 구체적인 에러 코드도 항상 DEFAULT fallback 문구로만 보였다.
describe("ExchangeManagementPage 에러 표시", () => {
  it("ApiError.errorCode가 있으면 매핑된 안내 문구를 보여주고 서버 원문은 노출하지 않는다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(422, "raw server detail", undefined, "EXCHANGE_FATAL"),
    );
    const { container } = renderPage();

    submitRegisterForm(container);

    await waitFor(() =>
      expect(screen.getByText("거래소 자격증명을 확인해주세요.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    const { container } = renderPage();

    submitRegisterForm(container);

    await waitFor(() => expect(screen.getByText("등록에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  it("traceId가 있으면 지원코드를 함께 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(500, "internal", "trace-exchange-1", "INTERNAL_ERROR"),
    );
    const { container } = renderPage();

    submitRegisterForm(container);

    await waitFor(() => expect(screen.getByText("지원코드: trace-exchange-1")).toBeInTheDocument());
  });
});
