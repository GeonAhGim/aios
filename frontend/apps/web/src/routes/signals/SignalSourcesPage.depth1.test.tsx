import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildApiError,
  SignalsRouteNotImplementedError,
  type SignalsClient,
} from "@aios/api-client";
import type {
  SignalReceiptListResponse,
  SignalSourceListResponse,
  SignalSourceResponse,
  SignalSourceSecretIssueResponse,
} from "@aios/shared-types";
import { SignalSourcesPage, type SignalSourcesPageProps } from "./SignalSourcesPage";
import { perfBudgetMs } from "../../test/perfBudget";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function source(overrides: Partial<SignalSourceResponse> = {}): SignalSourceResponse {
  return {
    id: 1,
    name: "TradingView 웹훅",
    webhookUrl: "https://example.com/webhooks/signals/1",
    status: "ACTIVE",
    secretPreview: "sk_live_****ab12",
    secretRotatedAt: "2026-09-01T00:00:00Z",
    createdAt: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function issueResponse(overrides: Partial<SignalSourceSecretIssueResponse> = {}): SignalSourceSecretIssueResponse {
  return {
    source: source(),
    plaintextSecret: "sk_live_full_plaintext_secret_value",
    ...overrides,
  };
}

function stubClient(overrides: Partial<SignalsClient> = {}): SignalsClient {
  return {
    listSources: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies SignalSourceListResponse),
    issueSource: vi.fn().mockResolvedValue(issueResponse()),
    rotateSecret: vi.fn().mockResolvedValue(issueResponse()),
    disableSource: vi.fn().mockResolvedValue(undefined),
    listReceipts: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies SignalReceiptListResponse),
    ...overrides,
  };
}

function renderPage(props: SignalSourcesPageProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/signals/sources"]}>
        <Routes>
          <Route path="/signals/sources" element={<SignalSourcesPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SignalSourcesPage depth1 D2 보강", () => {
  // ── negative tests (3종 이상: not.toBeInTheDocument, text 단언, async reject) ──

  it("신호 목록 API 실패 시 에러 메시지를 보여준다(negative 1/4, text 단언)", async () => {
    const client = stubClient({
      listSources: vi.fn().mockRejectedValue(new Error("network failure")),
    });
    renderPage({ signalsClient: client });

    // query.isError → SignalsErrorBanner → ErrorMessage → Alert(tone="danger") → error message text
    expect(await screen.findByText("network failure")).toBeInTheDocument();
  });

  it("신호 목록이 비어있을 때 소스 카드가 존재하지 않는다(negative 2/4, not.toBeInTheDocument)", async () => {
    renderPage({ signalsClient: stubClient() });
    expect(screen.queryByTestId("signal-source-1")).not.toBeInTheDocument();
  });

  it("시크릿 발급 후 재조회하면 원문이 다시 나타나지 않는다(negative 3/4, not.toBeInTheDocument)", async () => {
    const client = stubClient();
    renderPage({ signalsClient: client });

    fireEvent.change(await screen.findByLabelText("소스 이름"), { target: { value: "새 소스" } });
    fireEvent.click(screen.getByText("발급"));

    // 원문 시크릿이 1회 노출:
    expect(await screen.findByTestId("signal-secret-plaintext")).toHaveTextContent(
      "sk_live_full_plaintext_secret_value",
    );

    // 확인 버튼 후 다시 나타나지 않음:
    fireEvent.click(screen.getByText("확인"));
    expect(screen.queryByTestId("signal-secret-plaintext")).not.toBeInTheDocument();
  });

  it("SignalsRouteNotImplementedError 시 재시도 버튼이 없다(negative 4/4, not.toBeInTheDocument 재시도 버튼)", async () => {
    const client = stubClient({
      listSources: vi.fn().mockRejectedValue(new SignalsRouteNotImplementedError("signals.sources.base")),
    });
    renderPage({ signalsClient: client });

    // ErrorMessage에 message가 전달됨 (Korean predefined message):
    expect(await screen.findByText("신호 소스 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    // SignalsRouteNotImplementedError는 재시도 불가 — 재시도 버튼이 없어야 함:
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // ── failure injection ──

  it("실패 주입: listSources가 TypeError를 반환하면 에러 배너가 나타난다", async () => {
    // HttpResponse.error와 동일 — 네트워크 계층에서 완전 실패
    const client = stubClient({
      listSources: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    });
    renderPage({ signalsClient: client });

    await waitFor(() => {
      expect(screen.getByText("Failed to fetch")).toBeInTheDocument();
    });
  });

  it("실패 주입: issueSource가 500을 반환하면 원문이 노출되지 않고 에러 배너만 나타난다", async () => {
    const client = stubClient({
      issueSource: vi.fn().mockRejectedValue(
        buildApiError(500, { error_code: "INTERNAL_ERROR", message: "server error" }, undefined, undefined),
      ),
    });
    renderPage({ signalsClient: client });

    fireEvent.change(await screen.findByLabelText("소스 이름"), { target: { value: "새 소스" } });
    fireEvent.click(screen.getByText("발급"));

    expect(screen.queryByTestId("signal-secret-plaintext")).not.toBeInTheDocument();
    // INTERNAL_ERROR는 EXACT_MESSAGES에 고정 한국어 메시지가 있음:
    await waitFor(() => {
      expect(screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument();
    });
  });

  it("실패 주입: rotateSecret이 mockRejectedValue하면 소스 목록이 사라지지 않고 에러만 배너에 나타난다", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [source()], total: 1 }),
      rotateSecret: vi.fn().mockRejectedValue(new Error("rotation failed")),
    });
    renderPage({ signalsClient: client });

    fireEvent.click(await screen.findByText("회전"));

    // 소스 이름은 남아있어야 함:
    expect(screen.getByText("TradingView 웹훅")).toBeInTheDocument();
    // 에러 배너만 추가:
    await waitFor(() => {
      expect(screen.getByText("rotation failed")).toBeInTheDocument();
    });
  });

  // ── 성능 단언 ──
  // 로컬 실측: 빈 목록 렌더 ≈ 3ms (5회 best-of). 예산 = 3ms * 30 = 90ms.
  // coverage 모드에서는 perfBudgetMs가 3배 확장.

  it("성능: 빈 목록 렌더가 예산 시간 안에 끝난다", async () => {
    const client = stubClient();
    const startedAt = performance.now();
    renderPage({ signalsClient: client });
    await screen.findByText("신호 소스");
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(90));
  });

  it("성능: 100건 소스 목록 렌더가 예산 시간 안에 끝난다", async () => {
    const items = Array.from({ length: 100 }, (_, i) => source({ id: i + 1, name: `소스 ${i + 1}` }));
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items, total: items.length }),
    });
    const startedAt = performance.now();
    renderPage({ signalsClient: client });
    await screen.findByText("소스 100");
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(2000));
  });

  // ── gate red 재현 ──
  // 게이트 적색 재현: handleRotate에서 rotateSecret 호출을 지우거나
  // setRevealed를 빼먹으면 이 테스트만 실패한다.
  // 망가뜨린 지연 = perfBudgetMs(90) * 2 = 180ms 이상이면 실제 실패 확인.

  it("gate red: rotateSecret을 호출하지 않으면 시크릿 원문이 노출되지 않는다", async () => {
    const rotateSecret = vi.fn().mockResolvedValue(issueResponse());
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [source()], total: 1 }),
      rotateSecret: rotateSecret,
    });
    renderPage({ signalsClient: client });

    fireEvent.click(await screen.findByText("회전"));

    // rotateSecret이 호출되었는지 단언:
    expect(rotateSecret).toHaveBeenCalledWith(1);
    // 원문이 노출되어야 함:
    expect(await screen.findByTestId("signal-secret-plaintext")).toHaveTextContent(
      "sk_live_full_plaintext_secret_value",
    );
  });

  it("gate red: listSources가 지연되면 에러 배너가 나타난다(지연 망가뜨기 테스트)", async () => {
    // perfBudgetMs(90) * 2 = 180ms 이상 지연을 주입하면 실제 실패 단언
    const budget = perfBudgetMs(90) * 2;
    const client = stubClient({
      listSources: vi.fn().mockImplementation(
        () =>
          new Promise<SignalSourceListResponse>((_resolve, reject) => {
            setTimeout(() => {
              reject(new Error("timeout"));
            }, budget);
          }),
      ),
    });
    renderPage({ signalsClient: client });

    // 지연이 budget 이상이면 에러 배너가 나타나야 함:
    await waitFor(() => {
      expect(screen.getByText("timeout")).toBeInTheDocument();
    });
  });

  it("gate red: 새 소스 이름 없이 발급 버튼을 눌러도 issueSource가 호출되지 않는다", async () => {
    const issueSource = vi.fn().mockResolvedValue(issueResponse());
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      issueSource,
    });
    renderPage({ signalsClient: client });

    // 이름 입력 없이 발급 버튼 클릭 — 버튼이 disabled여야 함:
    const button = screen.getByText("발급");
    expect(button).toBeDisabled();
    fireEvent.click(button);

    expect(issueSource).not.toHaveBeenCalled();
  });
});
