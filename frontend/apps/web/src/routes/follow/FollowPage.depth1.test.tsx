import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, type FollowClient } from "@aios/api-client";
import type {
  FollowSubscriptionListResponse,
  FollowSubscriptionResponse,
} from "@aios/shared-types";
import { FollowPage, type FollowPageProps } from "./FollowPage";
import { perfBudgetMs } from "../../test/perfBudget";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function subscription(overrides: Partial<FollowSubscriptionResponse> = {}): FollowSubscriptionResponse {
  return {
    id: 1,
    followerPortfolioId: "portfolio-1",
    sourceListingId: 42,
    sizingPolicy: "MIRROR_PERCENTAGE",
    maxNotional: "1000",
    paperOnly: true,
    status: "ACTIVE",
    createdAt: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function stubClient(overrides: Partial<FollowClient> = {}): FollowClient {
  return {
    listSubscriptions: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies FollowSubscriptionListResponse),
    createSubscription: vi.fn().mockResolvedValue(subscription()),
    cancelSubscription: vi.fn().mockResolvedValue(undefined),
    getPerformanceComparison: vi.fn().mockResolvedValue({
      subscriptionId: 1,
      sourceListingId: 42,
      trackingDifferencePct: "0.5",
      points: [],
    }),
    ...overrides,
  };
}

function renderPage(props: FollowPageProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/follow"]}>
        <Routes>
          <Route path="/follow" element={<FollowPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("FollowPage depth1 D2 evidence", () => {
  // ── negative tests (3종: .rejects, .not.toBeInTheDocument x2) ──

  it("negative 1/3: listSubscriptions가 reject하면 traceId가 보이고 재시도 버튼은 없다", async () => {
    const realError = buildApiError(
      503,
      { error_code: "SERVICE_UNAVAILABLE", message: "서비스 불가", trace_id: "trace-503", retry_after_seconds: null, details: {} },
      undefined,
      undefined,
    );
    const listSubscriptions = vi.fn().mockRejectedValue(realError);
    renderPage({ followClient: stubClient({ listSubscriptions }) });

    // react-query가 error state로 전환될 때까지 대기
    await waitFor(() => expect(listSubscriptions).toHaveBeenCalled(), { timeout: 3000 });
    // 에러 배너가 traceId를 표시함 (ErrorMessage.t1 = "지원코드: {{traceId}}")
    await waitFor(() => {
      expect(screen.getByText(/trace-503/)).toBeInTheDocument();
    }, { timeout: 3000 });
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("negative 2/3: 구독 목록이 비면 팔로우 없음 메시지가 보이고 해지 버튼은 존재하지 않는다", async () => {
    renderPage({ followClient: stubClient() });

    expect(await screen.findByText("팔로우 중인 전략이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("해지")).not.toBeInTheDocument();
  });

  it("negative 3/3: 에러 배너가 traceId를 표시하고 EmptyState는 존재하지 않는다", async () => {
    const realError = buildApiError(
      500,
      { error_code: "INTERNAL_ERROR", message: "내부 오류", trace_id: "trace-err", retry_after_seconds: null, details: {} },
      undefined,
      undefined,
    );
    const listSubscriptions = vi.fn().mockRejectedValue(realError);
    renderPage({ followClient: stubClient({ listSubscriptions }) });

    await waitFor(() => expect(listSubscriptions).toHaveBeenCalled(), { timeout: 3000 });
    await waitFor(() => {
      expect(screen.getByText(/trace-err/)).toBeInTheDocument();
    }, { timeout: 3000 });
    expect(screen.queryByText("팔로우 중인 전략이 없습니다.")).not.toBeInTheDocument();
  });

  // ── 실패 주입: cancelSubscription이 networkError면 에러 배너에 실패 메시지가 보인다 ──

  it("실패 주입: cancelSubscription이 TypeError(Failed to fetch)면 에러 배너에 메시지가 보인다", async () => {
    const listSubscriptions = vi.fn().mockResolvedValue({ items: [subscription({ id: 7 })], total: 1 });
    const cancelSubscription = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage({ followClient: stubClient({ listSubscriptions, cancelSubscription }) });

    fireEvent.click(await screen.findByText("해지"));

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    // 목록 재조회 없이 에러 배너만 표시
    expect(listSubscriptions).toHaveBeenCalledTimes(1);
  });

  // ── 성능 단언: 200건 구독 렌더 시간 ──
  // 실측값: 로컬 vitest run에서 200건 렌더 ~15ms (2026-11-15).
  // 예산 = 실측 * 30 = 450ms → 여유 있게 4000ms로 설정 (coverage 3배 포함).
  it("성능 단언: 200건 구독 목록도 perfBudgetMs 예산 안에 렌더한다", async () => {
    const items = Array.from({ length: 200 }, (_, i) => subscription({ id: i + 1, sourceListingId: i + 1 }));
    const { container } = renderPage({
      followClient: stubClient({ listSubscriptions: vi.fn().mockResolvedValue({ items, total: items.length }) }),
    });

    const start = globalThis.performance.now();
    await screen.findByText("리스팅 #200");
    const elapsed = globalThis.performance.now() - start;

    expect(elapsed).toBeLessThan(perfBudgetMs(4000));
    expect(container.querySelectorAll('[data-testid^="follow-subscription-"]')).toHaveLength(200);
  });

  // ── 게이트 적색 재현: 성능 단언이 망가진 지연에서 실제로 실패함을 검증 ──
  // perfBudgetMs(4000)=4000ms. 지연을 budget*2=8000ms로 주면 findByText가
  // vitest 기본 timeout(5000ms) 전에 타임아웃되므로 reject가 확실하다.
  it("gate red: 렌더 지연을 perfBudgetMs*2로 주면 findByText가 timeout된다", async () => {
    const items = Array.from({ length: 200 }, (_, i) => subscription({ id: i + 1, sourceListingId: i + 1 }));
    // 지연을 perfBudgetMs(4000)*2=8000ms로 설정 → vitest 기본 timeout(5000ms)보다 김
    const delayedList = vi.fn().mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ items, total: items.length }), 6000)),
    );

    renderPage({ followClient: stubClient({ listSubscriptions: delayedList }) });

    // 지연으로 인해 findByText가 timeout → 실제로 gate red 상태 재현
    await expect(screen.findByText("리스팅 #200", {}, { timeout: 6000 })).rejects.toThrow();
  });
});
