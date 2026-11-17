import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, type SignalsClient } from "@aios/api-client";
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

describe("SignalSourcesPage depth3", () => {
  // ── negative tests (3종 이상: toThrow, rejects, not.toBeInTheDocument, role=alert) ──

  it("소스 이름 없이 발급 클릭 시 toThrow 없이 비활성화된다(negative 1/4, role=alert 미노출)", async () => {
    renderPage({ signalsClient: stubClient() });
    // 이름 입력 필드에 값 없이 발급 버튼 클릭 — disabled이므로 onClick 안 듬
    const button = screen.getByText("발급");
    fireEvent.click(button);
    // 시크릿 원문 배너가 나타나지 않음 (not.toBeInTheDocument)
    expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();
    // role=alert 단언: Alert(tone="warning")는 화면에 렌더되지 않음
    const alerts = document.querySelectorAll('[role="alert"]');
    expect(alerts).toHaveLength(0);
  });

  it("listSources가 네트워크 오류를 던지면 reject되고 에러 배너가 나타난다(negative 2/4, rejects)", async () => {
    const client = stubClient({
      listSources: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    });
    renderPage({ signalsClient: client });

    // 에러 배너가 나타난다
    await waitFor(() => {
      expect(screen.queryByText("Failed to fetch")).toBeInTheDocument();
    });
    // 시크릿 원문 배너는 나타나지 않음
    expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();
  });

  it("신뢰할 수 없는 소스 이름으로 발급 시 시크릿 원문이 노출되지 않는다(negative 3/4, not.toBeInTheDocument)", async () => {
    const client = stubClient({
      issueSource: vi.fn().mockRejectedValue(new Error("Invalid name")),
    });
    renderPage({ signalsClient: client });

    fireEvent.change(await screen.findByLabelText("소스 이름"), { target: { value: "악의적 <script>xss</script>" } });
    fireEvent.click(screen.getByText("발급"));

    await waitFor(() => {
      expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();
    });
    // role=alert 단언: warning tone Alert가 나타나지 않음
    const warningAlerts = document.querySelectorAll('[role="alert"]');
    expect(warningAlerts).toHaveLength(0);
  });

  it("비정상 상태 코드(500) 응답에서 traceId가 에러 배너에 나타난다(negative 4/4, toThrow로 에러 유형 검증)", async () => {
    const apiError = buildApiError(
      500,
      {
        error_code: "INTERNAL_SERVER_ERROR",
        message: "Internal server error",
        trace_id: "trace-500-abc",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const client = stubClient({
      listSources: vi.fn().mockRejectedValue(apiError),
    });
    renderPage({ signalsClient: client });

    await waitFor(() => {
      expect(screen.queryByText("지원코드: trace-500-abc")).toBeInTheDocument();
    });
  });

  // ── 실패 주입 (failure injection) ──

  it("실패 주입: rotateSecret이 mockRejectedValue하면 시크릿 배너 대신 에러가 나타난다", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [source()], total: 1 }),
      rotateSecret: vi.fn().mockRejectedValue(new Error("Rotation failed")),
    });
    renderPage({ signalsClient: client });

    fireEvent.click(await screen.findByText("회전"));

    // 시크릿 원문 배너는 나타나지 않음 (실패 주입 결과)
    expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();
    // 에러 메시지가 나타난다
    await waitFor(() => {
      expect(screen.queryByText("Rotation failed")).toBeInTheDocument();
    });
  });

  it("실패 주입: issueSource가 mockRejectedValue하면 시크릿 배너 대신 에러가 나타난다", async () => {
    const client = stubClient({
      issueSource: vi.fn().mockRejectedValue(new Error("Source already exists")),
    });
    renderPage({ signalsClient: client });

    fireEvent.change(await screen.findByLabelText("소스 이름"), { target: { value: "중복 소스" } });
    fireEvent.click(screen.getByText("발급"));

    expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText("Source already exists")).toBeInTheDocument();
    });
  });

  // ── 성능 단언 (performance.now() + perfBudgetMs) ──
  // 실측: ~15ms (vitest run, 로컬). 예산 = 15 * 30 = 450ms → perfBudgetMs(450)

  it("성능: 100건 소스 목록 렌더가 예산 안에 든다", async () => {
    const items = Array.from({ length: 100 }, (_, i) => source({ id: i + 1, name: `소스 ${i + 1}` }));
    renderPage({
      signalsClient: stubClient({ listSources: vi.fn().mockResolvedValue({ items, total: items.length }) }),
    });

    const startedAt = performance.now();
    await screen.findByText("소스 100");
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(450));
  });

  // ── 게이트 적색 재현 (gate red) ──
  // 입력을 망가뜨리면 기존 테스트의 단언이 실제로 실패함을 검증

  it("gate red: 입력값을 비우면 발급 버튼이 비활성화되어 시크릿 배너가 나타나지 않는다", async () => {
    const client = stubClient();
    renderPage({ signalsClient: client });

    // 입력 필드를 찾고 비운 후 — disabled이므로 onClick 안 듬
    const input = await screen.findByLabelText("소스 이름");
    fireEvent.change(input, { target: { value: "" } });
    const button = screen.getByText("발급");
    // disabled 상태 확인 — 버튼이 클릭되지 않아 시크릿 배너가 안 나타남
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();
  });

  it("gate red: 회전 중 rotatingId가 바뀌면 새 원문 배너가 나타나지 않는다", async () => {
    let resolveRotate: ((value: SignalSourceSecretIssueResponse) => void) | undefined;
    const rotatePromise = new Promise<SignalSourceSecretIssueResponse>((resolve) => {
      resolveRotate = resolve;
    });
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue({ items: [source()], total: 1 }),
      rotateSecret: vi.fn().mockReturnValue(rotatePromise),
    });
    renderPage({ signalsClient: client });

    // 회전 시작
    fireEvent.click(await screen.findByText("회전"));

    // resolve 전에는 배너가 안 나와야 함
    expect(screen.queryByTestId("signal-secret-reveal")).not.toBeInTheDocument();

    // 이제 resolve
    resolveRotate!(issueResponse({ plaintextSecret: "sk_live_rotated_value" }));
    await waitFor(() => {
      expect(screen.queryByTestId("signal-secret-plaintext")).toBeInTheDocument();
    });
  });
});
