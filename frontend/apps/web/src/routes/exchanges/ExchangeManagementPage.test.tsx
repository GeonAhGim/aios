import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { ExchangeManagementPage } from "./ExchangeManagementPage";

const mutateAsync = vi.fn();
const refetch = vi.fn();
let credentialsData: unknown[] = [];
let revokeMutateAsync = vi.fn().mockResolvedValue(undefined);
vi.mock("@aios/shared-hooks", () => ({
  useExchangeCredentials: () => ({ data: credentialsData, isLoading: false, refetch }),
  useRegisterExchangeCredential: () => ({ mutateAsync, isPending: false }),
  useRevokeExchangeCredential: () => ({ mutateAsync: revokeMutateAsync }),
  useExchangeBalance: () => ({ data: undefined }),
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
  refetch.mockReset();
  credentialsData = [];
  revokeMutateAsync = vi.fn().mockResolvedValue(undefined);
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ExchangeManagementPage />
    </MemoryRouter>,
  );
}

function credential(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    exchange: "bitget",
    isActive: true,
    linkedAt: "2026-01-01T00:00:00Z",
    withdrawalPermissionWarning: null,
    ...overrides,
  };
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

  // task-910 §3.3: routeApiError 경유 BadRequestNotice 경로 — 알려진 400 코드는
  // raw server detail 대신 고정 매핑 문구를 보여준다.
  it("negative: VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400)는 BadRequestNotice 경로로 새로고침 안내를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "raw detail", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"),
    );
    const { container } = renderPage();

    submitRegisterForm(container);

    await waitFor(() =>
      expect(
        screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
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

// task-482 §3.6: 목록의 각 항목은 task-473 parseSecretRef 결과로 scope 배지를
// 표시한다. 파싱 실패는 scope를 추측하지 않고 "알 수 없음"으로 둔다.
describe("ExchangeManagementPage scope 배지", () => {
  it("secretRef가 secref://paper/... 면 PAPER 배지를 보여준다", () => {
    credentialsData = [
      credential({ secretRef: "secref://paper/exchange_credential/1@v1" }),
    ];
    renderPage();

    expect(screen.getByText("PAPER")).toBeInTheDocument();
  });

  it("secretRef가 secref://live/... 면 LIVE 배지 + POLICY_LIVE_BLOCKED 안내문을 보여주고 해지 버튼을 비활성화한다", () => {
    credentialsData = [
      credential({ secretRef: "secref://live/exchange_credential/1@v1" }),
    ];
    renderPage();

    expect(screen.getByText("LIVE")).toBeInTheDocument();
    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "해지" })).toBeDisabled();
  });

  it("negative: secretRef가 없거나 형식이 깨졌으면 scope를 추측하지 않고 '알 수 없음'을 보여준다", () => {
    credentialsData = [
      credential({ id: 1, secretRef: undefined }),
      credential({ id: 2, secretRef: "not-a-secret-ref" }),
    ];
    renderPage();

    const unknownBadges = screen.getAllByText("알 수 없음");
    expect(unknownBadges).toHaveLength(2);
    expect(screen.queryByText("LIVE")).not.toBeInTheDocument();
    expect(screen.queryByText("PAPER")).not.toBeInTheDocument();
    for (const button of screen.getAllByRole("button", { name: "해지" })) {
      expect(button).not.toBeDisabled();
    }
  });
});

// task-482 ADR-2026-08-29-E: 등록 폼은 PAPER scope로만 제출되고, LIVE 제출 경로가
// 코드상 존재하지 않는다(스코프 선택 UI 자체가 없다).
describe("ExchangeManagementPage 등록 폼 scope 상한", () => {
  it("등록 요청 payload에 scope 필드가 없다 — PAPER 기본값 외 제출 경로가 없다", async () => {
    mutateAsync.mockResolvedValue(undefined);
    const { container } = renderPage();

    submitRegisterForm(container);
    await waitFor(() => expect(mutateAsync).toHaveBeenCalled());

    const payload = mutateAsync.mock.calls[0][0];
    expect(Object.keys(payload)).not.toContain("scope");
  });

  it("negative: 등록 폼에는 LIVE/scope를 선택할 수 있는 컨트롤이 없다", () => {
    renderPage();
    expect(screen.queryByText(/LIVE/i)).not.toBeInTheDocument();
  });
});

// task-482 §3.3: 해지 요청이 403 POLICY_LIVE_BLOCKED로 거부되면 task-382의
// extractReasonCodes 경로(ForbiddenNotice)로 표시한다 — 새 안내 컴포넌트를 만들지 않는다.
describe("ExchangeManagementPage 해지 403 POLICY_LIVE_BLOCKED", () => {
  it("ForbiddenNotice를 통해 정책 거부 안내를 보여준다", async () => {
    credentialsData = [credential()];
    revokeMutateAsync = vi
      .fn()
      .mockRejectedValue(new ApiError(403, "blocked", undefined, "POLICY_LIVE_BLOCKED"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "해지" }));

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
  });
});

// task-937 §3.3: 5xx는 classifyServerError, 409 STATE_CONCURRENCY_CONFLICT는
// useConflictRetry로 처리한다.
describe("ExchangeManagementPage 5xx·409 재시도 배선", () => {
  it("negative: EXCHANGE_UNAVAILABLE(503)은 재시도 안내와 함께 다시 시도 버튼을 보여준다", async () => {
    mutateAsync.mockRejectedValue(new ApiError(503, "raw", undefined, "EXCHANGE_UNAVAILABLE"));
    const { container } = renderPage();

    submitRegisterForm(container);

    await waitFor(() =>
      expect(
        screen.getByText("거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeEnabled();
  });

  it("negative: STATE_CONCURRENCY_CONFLICT(409)는 연동 목록을 refetch한 뒤 새 Idempotency-Key로 자동 재제출해 성공한다", async () => {
    mutateAsync
      .mockRejectedValueOnce(new ApiError(409, "충돌", undefined, "STATE_CONCURRENCY_CONFLICT"))
      .mockResolvedValueOnce(undefined);
    const { container } = renderPage();

    submitRegisterForm(container);

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/다른 요청과 충돌했습니다/)).not.toBeInTheDocument();
  });
});
