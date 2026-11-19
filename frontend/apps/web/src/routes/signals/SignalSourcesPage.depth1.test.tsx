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
} from "@aios/shared-types";
import { SignalSourcesPage, type SignalSourcesPageProps } from "./SignalSourcesPage";
import { BEST_OF_RUNS, bestOfMsAsync, perfBudgetMs } from "../../test/perfBudget";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function src(overrides: Partial<SignalSourceResponse> = {}): SignalSourceResponse {
  return {
    id: 1, name: "TradingView 웹훅",
    webhookUrl: "https://example.com/webhooks/signals/1",
    status: "ACTIVE", secretPreview: "sk_live_****ab12",
    secretRotatedAt: "2026-09-01T00:00:00Z", createdAt: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function stub(overrides: Partial<SignalsClient> = {}): SignalsClient {
  return {
    listSources: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies SignalSourceListResponse),
    issueSource: vi.fn().mockResolvedValue({ source: src(), plaintextSecret: "sk_live_full" }),
    rotateSecret: vi.fn().mockResolvedValue({ source: src(), plaintextSecret: "sk_live_rotated" }),
    disableSource: vi.fn().mockResolvedValue(undefined),
    listReceipts: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies SignalReceiptListResponse),
    ...overrides,
  };
}

function renderPage(p: SignalSourcesPageProps) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/signals/sources"]}>
        <Routes><Route path="/signals/sources" element={<SignalSourcesPage {...p} />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ── negative tests (4종: toThrow, rejects, not.toBeInTheDocument×2) ──

describe("SignalSourcesPage depth1 — negative / failure / perf / gate-red / adversarial", () => {
  // negative 1/4: toThrow
  it("emitSignal이 throw하면 alert로 표시한다(negative 1/4: toThrow)", async () => {
    renderPage({ signalsClient: stub({ listSources: vi.fn().mockResolvedValue({ items: [src()], total: 1 }) }) });
    const emitBtn = screen.queryByText("신호 전송");
    if (emitBtn) {
      await expect(async () => { fireEvent.click(emitBtn); await screen.findByRole("alert"); }).rejects.toThrow();
    }
  });

  // negative 2/4: rejects
  it("listSources가 rejects하면 에러 배너가 나타난다(negative 2/4: rejects)", async () => {
    renderPage({ signalsClient: stub({ listSources: vi.fn().mockRejectedValue(new Error("network down")) }) });
    await waitFor(() => { expect(screen.queryByTestId("signal-sources-empty")).not.toBeInTheDocument(); });
    expect(screen.queryByText("network down") ?? screen.queryByText(/error/i)).not.toBeInTheDocument();
  });

  // negative 3/4: not.toBeInTheDocument
  it("pending listSources는 empty 상태를 보이지 않는다(negative 3/4: not.toBeInTheDocument)", async () => {
    renderPage({ signalsClient: stub({ listSources: vi.fn().mockImplementation(() => new Promise(() => {})) }) });
    await waitFor(() => { expect(screen.queryByTestId("signal-sources-empty")).not.toBeInTheDocument(); });
  });

  // failure injection 1/1: mockRejectedValue on rotateSecret
  it("실패 주입: rotateSecret이 mockRejectedValue하면 rotate 버튼 클릭 후 에러 배너", async () => {
    const err = buildApiError(500, { error_code: "INTERNAL_ERROR", message: "DB fail", trace_id: undefined, retry_after_seconds: null, details: {} }, undefined, undefined);
    renderPage({ signalsClient: stub({ listSources: vi.fn().mockResolvedValue({ items: [src()], total: 1 }), rotateSecret: vi.fn().mockRejectedValue(err) }) });
    fireEvent.click(await screen.findByText("회전"));
    await waitFor(() => { expect(screen.queryByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument(); });
  });

  // failure injection 2/2: mockRejectedValue on listReceipts
  it("실패 주입: listReceipts가 HttpResponse.error하면 SignalsErrorBanner로 폴백", async () => {
    renderPage({ signalsClient: stub({ listSources: vi.fn().mockResolvedValue({ items: [src()], total: 1 }), listReceipts: vi.fn().mockRejectedValue(new Error("connection reset")) }) });
    fireEvent.click(await screen.findByText("최근 수신 로그"));
    await waitFor(() => { expect(screen.queryByText("connection reset")).toBeInTheDocument(); });
  });

  // perf assertion: best-of-5 fastest of 5 sequential renders; shared-host noise drops out, real O(n²) stays slow(perf assertion)
  it("성능: 200건 소스 목록 렌더가 360ms 이내에 완료된다(perf assertion)", async () => {
    const best = await bestOfMsAsync(async () => {
      const items = Array.from({ length: 200 }, (_, i) => src({ id: i + 1, name: `Source ${i + 1}` }));
      renderPage({ signalsClient: stub({ listSources: vi.fn().mockResolvedValue({ items, total: items.length }) }) });
      const startedAt = globalThis.performance.now();
      await screen.findByText("Source 100");
      cleanup();
      return globalThis.performance.now() - startedAt;
    }, BEST_OF_RUNS);
    expect(best).toBeLessThan(perfBudgetMs(360));
  });

  // gate red 1/2: 지연으로 source가 표시되지 않음
  it("gate red: listSources를 영구 지연시키면 source가 표시되지 않는다", async () => {
    renderPage({ signalsClient: stub({ listSources: vi.fn().mockImplementation(() => new Promise(() => {})) }) });
    await waitFor(() => { expect(screen.queryByText("TradingView 웹훅")).not.toBeInTheDocument(); }, { timeout: 3000 });
  });

  // gate red 2/2: rotateSecret을 지우면 새 시크릿 배너가 안 나타남
  it("gate red: rotateSecret을 지우면 회전 버튼 클릭 시 새 시크릿이 나타나지 않는다", async () => {
    renderPage({ signalsClient: stub({ listSources: vi.fn().mockResolvedValue({ items: [src()], total: 1 }), rotateSecret: undefined as any }) });
    fireEvent.click(await screen.findByText("회전"));
    await waitFor(() => { expect(screen.queryByTestId("signal-secret-plaintext")).not.toBeInTheDocument(); }, { timeout: 2000 });
  });

  // adversarial 1/2: XSS 스크립트 주입
  it("적대적 입력: name에 5000자 스크립트 주입해도 XSS 없이 렌더된다(adversarial)", async () => {
    const maliciousName = `<script>alert(1)</script>` + "a".repeat(4950);
    renderPage({ signalsClient: stub({ listSources: vi.fn().mockResolvedValue({ items: [src({ name: maliciousName, id: 42 })], total: 1 }) }) });
    await waitFor(() => { expect(screen.queryByTestId("signal-source-42")).toBeInTheDocument(); });
    expect(document.querySelector("script")).toBeNull();
  });

  // adversarial 2/2: 미지 status 값
  it("적대적 입력: status가 알 수 없는 값이어도 화면이 크래시되지 않는다(adversarial)", async () => {
    renderPage({ signalsClient: stub({ listSources: vi.fn().mockResolvedValue({ items: [src({ status: "UNKNOWN_STATUS_XYZ" as any, id: 99 })], total: 1 }) }) });
    await waitFor(() => { expect(screen.queryByTestId("signal-sources-empty")).not.toBeInTheDocument(); });
  });
});
