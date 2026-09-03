import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AiosApiClient, ApiError } from "@aios/api-client";
import type { TopupRequestBody } from "@aios/shared-types";
import { WalletPage } from "./WalletPage";

const mutateAsync = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useWalletBalance: () => ({ data: { balance: "1000" }, isLoading: false }),
  useRequestTopup: () => ({ mutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
  vi.unstubAllGlobals();
});

// task-1049 §3.7/§9 PLT-15: mutateAsync를 단순 spy로 두면 컴포넌트가 어떤 문자열을
// 넘겼는지만 확인하는 동어반복이 된다 — 아래 describe는 mutateAsync가 실제
// AiosApiClient(멱등 헤더 조립 포함)에 위임하도록 해 fetch로 나간 진짜
// Idempotency-Key 헤더값을 단언한다.
const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,128}$/;
const realClient = new AiosApiClient("https://api.example.test", () => null);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function idempotencyKeyOf(fetchMock: ReturnType<typeof vi.fn>): string | null {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return new Headers(init.headers).get("Idempotency-Key");
}

function delegateToRealClient() {
  mutateAsync.mockImplementation(
    (vars: { body: TopupRequestBody; idempotencyKey: string }) =>
      realClient.requestTopup(vars.body, vars.idempotencyKey),
  );
}

function renderPage() {
  render(
    <MemoryRouter>
      <WalletPage />
    </MemoryRouter>,
  );
}

function submitTopup() {
  fireEvent.click(screen.getByRole("button", { name: "충전 신청" }));
}

// task-322 후속 결함: errorCode가 ErrorMessage에 전달되지 않아 EXCHANGE_FATAL 등
// 구체적인 에러 코드도 항상 DEFAULT fallback 문구로만 보였다.
describe("WalletPage 에러 표시", () => {
  it("ApiError.errorCode가 있으면 매핑된 안내 문구를 보여주고 서버 원문은 노출하지 않는다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(409, "raw server detail", undefined, "STATE_CONCURRENCY_CONFLICT"),
    );
    renderPage();

    submitTopup();

    await waitFor(() =>
      expect(
        screen.getByText("다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    submitTopup();

    await waitFor(() => expect(screen.getByText("충전 요청에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  it("traceId가 있으면 지원코드를 함께 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(500, "internal", "trace-wallet-1", "INTERNAL_ERROR"),
    );
    renderPage();

    submitTopup();

    await waitFor(() => expect(screen.getByText("지원코드: trace-wallet-1")).toBeInTheDocument());
  });

  // task-930 §9 PLT-25: 429 RATE_LIMIT_EXCEEDED는 routeApiError의 backoff_retry로
  // 판정돼 재시도 버튼이 카운트다운 후 활성화된다.
  it("negative: 429 RATE_LIMIT_EXCEEDED는 retry_after 카운트다운 후 재시도 버튼을 활성화한다", async () => {
    vi.useFakeTimers();
    try {
      mutateAsync.mockRejectedValue(
        new ApiError(429, "too many requests", "trace-wallet-2", "RATE_LIMIT_EXCEEDED", 2),
      );
      renderPage();

      await act(async () => submitTopup());

      expect(
        screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument();
      const retryButton = screen.getByRole("button", { name: "다시 시도" });
      expect(retryButton).toBeDisabled();
      expect(screen.getByText("2초 후 재시도 가능")).toBeInTheDocument();

      await act(async () => vi.advanceTimersByTime(1000));
      await act(async () => vi.advanceTimersByTime(1000));
      expect(retryButton).toBeEnabled();

      vi.useRealTimers();
      mutateAsync.mockResolvedValueOnce({ id: 7, requestedAmount: "30000" });
      retryButton.click();
      await waitFor(() => expect(screen.getByText(/충전 요청 #7/)).toBeInTheDocument());
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("WalletPage 충전 신청 Idempotency-Key(§3.7) 실제 헤더 검증", () => {
  it("제출하면 실제 요청 헤더에 규격(16~128자, [A-Za-z0-9_-])을 만족하는 Idempotency-Key를 싣는다", async () => {
    const fetchMock = stubFetch({ id: 1, requested_amount: "30000", status: "PENDING" });
    delegateToRealClient();
    renderPage();

    submitTopup();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(idempotencyKeyOf(fetchMock)).toMatch(IDEMPOTENCY_KEY_RE);
  });

  it("negative: 409 STATE_CONCURRENCY_CONFLICT 후 재제출하면 새 Idempotency-Key로 다시 보낸다", async () => {
    const firstFetch = stubFetch(
      {
        error_code: "STATE_CONCURRENCY_CONFLICT",
        message: "충돌",
        details: {},
        trace_id: "t-wallet-409",
        retry_after_seconds: null,
      },
      409,
    );
    delegateToRealClient();
    renderPage();

    submitTopup();
    await waitFor(() =>
      expect(
        screen.getByText("다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    const firstKey = idempotencyKeyOf(firstFetch);
    expect(firstKey).toMatch(IDEMPOTENCY_KEY_RE);

    const secondFetch = stubFetch({ id: 2, requested_amount: "30000", status: "PENDING" });
    submitTopup();
    await waitFor(() => expect(secondFetch).toHaveBeenCalledTimes(1));
    expect(idempotencyKeyOf(secondFetch)).not.toBe(firstKey);
  });

  it("negative: 429 RATE_LIMIT_EXCEEDED도 키를 폐기해 재시도 버튼 클릭 시 새 Idempotency-Key로 나간다", async () => {
    const firstFetch = stubFetch(
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 너무 많습니다",
        details: {},
        trace_id: "t-wallet-429",
        retry_after_seconds: 0,
      },
      429,
    );
    delegateToRealClient();
    renderPage();

    submitTopup();
    await waitFor(() =>
      expect(
        screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    const firstKey = idempotencyKeyOf(firstFetch);

    const secondFetch = stubFetch({ id: 3, requested_amount: "30000", status: "PENDING" });
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(secondFetch).toHaveBeenCalledTimes(1));
    expect(idempotencyKeyOf(secondFetch)).not.toBe(firstKey);
  });
});
