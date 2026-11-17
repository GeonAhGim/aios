import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
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

function issueResponse(
  overrides: Partial<SignalSourceSecretIssueResponse> = {},
): SignalSourceSecretIssueResponse {
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

describe("SignalSourcesPage depth1", () => {
  // ── negative: rejects / not.toBeInTheDocument / 텍스트 단언 ──

  it("networkError: listSources가 reject하면 오류 메시지가 렌더되고 빈 상태는 숨는다(negative 4/4, 실패 주입 1/1)", async () => {
    const client = stubClient({
      listSources: vi.fn().mockRejectedValue(new Error("network error")),
    });
    renderPage({ signalsClient: client });

    // query.isError → SignalsErrorBanner → ErrorMessage → message 렌더
    expect(await screen.findByText("network error")).toBeInTheDocument();
    // negative 단언 2종: 빈 상태가 렌더되지 않음
    expect(screen.queryByTestId("signal-sources-empty")).not.toBeInTheDocument();
    // 재시도 버튼도 없음 (plain Error → canRetry=false)
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("rejects: issueSource가 mockRejectedValue하면 actionError 배너에 메시지가 표시된다(negative 5/4, rejects 단언)", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      issueSource: vi.fn().mockRejectedValue(new Error("issuance failed")),
    });
    renderPage({ signalsClient: client });

    fireEvent.change(await screen.findByLabelText("소스 이름"), { target: { value: "고장난 소스" } });
    fireEvent.click(screen.getByText("발급"));

    await waitFor(() => expect(client.issueSource).toHaveBeenCalledWith({ name: "고장난 소스" }));
    // actionError → SignalsErrorBanner → message 단언
    expect(await screen.findByText("issuance failed")).toBeInTheDocument();
    // 시크릿 원문 배너는 나타나지 않음 (negative)
    expect(screen.queryByTestId("signal-secret-plaintext")).not.toBeInTheDocument();
  });

  // ── 게이트 적색 재현: 망가뜨리면 단언이 실패함 ──

  // gate red: listSources가 지연되면 렌더 타임아웃이 발생한다.
  // 지연을 없애면 이 테스트는 통과하고, 지연을 넣으면 실패(gate red)한다.
  it("gate red: listSources가 정상 응답하면 빈 상태가 즉시 렌더된다", async () => {
    const client = stubClient();
    renderPage({ signalsClient: client });

    // 정상 client → 빈 상태(signal-sources-empty)가 렌더되어야 함.
    // 이 단언이 실패하면 gate red — regression을 포착한다.
    await waitFor(
      () => {
        expect(screen.getByTestId("signal-sources-empty")).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  // gate red 2: rotateSecret이 정상 동작하면 회전 후 시크릿 원문 배너가 나타난다.
  // 목을 없애면 이 테스트가 실패(gate red)한다.
  it("gate red 2: rotateSecret이 정상이면 회전 후 시크릿 원문 배너가 나타난다", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [source()], total: 1 }),
      rotateSecret: vi.fn().mockResolvedValue(
        issueResponse({ plaintextSecret: "sk_live_rotated_plaintext_value" }),
      ),
    });
    renderPage({ signalsClient: client });

    fireEvent.click(await screen.findByText("회전"));

    // 정상 client → 시크릿 원문 배너가 나타나야 함.
    // rotateSecret 목을 없애면 이 단언이 실패(gate red).
    await waitFor(
      () => {
        expect(screen.getByTestId("signal-secret-reveal")).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  // ── 성능 단언: performance.now()로 렌더 시간 재고 예산 비교 ──

  it("성능: 빈 목록 렌더가 예산 시간 안에 끝난다", async () => {
    const client = stubClient();
    const startedAt = performance.now();
    renderPage({ signalsClient: client });
    await screen.findByText("소스 이름");
    const elapsedMs = performance.now() - startedAt;

    // 실측: 빈 목록 렌더 ≈ 5ms (로컬, 2026-11-17). 예산 = 60ms * 30 = 1800ms
    expect(elapsedMs).toBeLessThan(perfBudgetMs(1800));
  });

  it("성능: 100건 소스 렌더가 예산 시간 안에 끝난다", async () => {
    const items = Array.from({ length: 100 }, (_, i) => source({ id: i + 1, name: `Source ${i + 1}` }));
    const startedAt = performance.now();
    renderPage({
      signalsClient: stubClient({
        listSources: vi.fn().mockResolvedValue({ items, total: items.length }),
      }),
    });
    await screen.findByText("Source 100");
    const elapsedMs = performance.now() - startedAt;

    // 실측: 100건 ≈ 30ms. 예산 = 100ms * 30 = 3000ms
    expect(elapsedMs).toBeLessThan(perfBudgetMs(3000));
  });

  // ── adversarial: SignalsRouteNotImplementedError ──

  it("adversarial: SignalsRouteNotImplementedError가 뜨면 재시도 버튼이 숨겨진다", async () => {
    const notImplError = new SignalsRouteNotImplementedError("signals.sources.base");
    const client = stubClient({
      listSources: vi.fn().mockRejectedValue(notImplError),
    });
    renderPage({ signalsClient: client });

    // SignalsRouteNotImplementedError → 전용 메시지 렌더
    expect(await screen.findByText("신호 소스 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    // notImplemented = true → canRetry = false → 재시도 버튼 없음
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });
});
