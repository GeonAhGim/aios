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
// getByLabelText 대신 input[type] 순서로 값을 채운다. apiKey/apiSecret/apiPassphrase
// 모두 type="password"라 폼에 등장하는 순서(API Key, API Secret, API Passphrase)로 찾는다.
function passwordInputs(container: HTMLElement) {
  return [...container.querySelectorAll('input[type="password"]')] as HTMLInputElement[];
}

function submitRegisterForm(
  container: HTMLElement,
  values: { apiKey: string; apiSecret: string; apiPassphrase: string } = {
    apiKey: "key-1",
    apiSecret: "secret-1",
    apiPassphrase: "pass-1",
  },
) {
  const [apiKey, apiSecret, apiPassphrase] = passwordInputs(container);
  fireEvent.change(apiKey, { target: { value: values.apiKey } });
  fireEvent.change(apiSecret, { target: { value: values.apiSecret } });
  fireEvent.change(apiPassphrase, { target: { value: values.apiPassphrase } });
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

// task-473 §3.6: 클라이언트는 비밀을 저장·복호하지 않으므로 폼 상태는 제출 성공/실패
// 양쪽에서 즉시 폐기되어야 하고, 서버가 에러 메시지에 비밀을 반향하더라도 화면에
// 원문이 남으면 안 된다.
describe("ExchangeManagementPage 비밀 폐기·리댁션", () => {
  it("제출 성공 시 apiKey/apiSecret/apiPassphrase 입력을 즉시 비운다", async () => {
    mutateAsync.mockResolvedValue(undefined);
    const { container } = renderPage();

    submitRegisterForm(container);
    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());

    const [apiKey, apiSecret, apiPassphrase] = passwordInputs(container);
    await waitFor(() => expect(apiKey.value).toBe(""));
    expect(apiSecret.value).toBe("");
    expect(apiPassphrase.value).toBe("");
  });

  it("제출 실패 시에도 apiSecret/apiPassphrase 입력을 즉시 비운다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    const { container } = renderPage();

    submitRegisterForm(container);
    await waitFor(() => expect(screen.getByText("등록에 실패했습니다.")).toBeInTheDocument());

    const [, apiSecret, apiPassphrase] = passwordInputs(container);
    expect(apiSecret.value).toBe("");
    expect(apiPassphrase.value).toBe("");
  });

  it("negative: 매핑되지 않은 에러가 입력한 비밀을 그대로 반향해도 화면·입력 상태 어디에도 원문이 남지 않는다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, 'invalid field api_secret="leaked-secret-value-0001"', undefined),
    );
    const { container } = renderPage();

    submitRegisterForm(container, {
      apiKey: "key-1",
      apiSecret: "leaked-secret-value-0001",
      apiPassphrase: "pass-1",
    });

    await waitFor(() => expect(screen.getByText(/invalid field/)).toBeInTheDocument());
    expect(screen.queryByText(/leaked-secret-value-0001/)).not.toBeInTheDocument();

    const [, apiSecret] = passwordInputs(container);
    expect(apiSecret.value).toBe("");
  });
});
