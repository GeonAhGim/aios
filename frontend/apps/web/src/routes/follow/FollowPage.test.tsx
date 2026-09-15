import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApiError, FollowRouteNotImplementedError, type FollowClient } from "@aios/api-client";
import { findAdvisoryLanguageViolations } from "@aios/shared-types";
import type {
  FollowPerformanceComparisonResponse,
  FollowSubscriptionListResponse,
  FollowSubscriptionResponse,
} from "@aios/shared-types";
import { auditFollowPageAdvisoryLanguage, FollowPage, type FollowPageProps } from "./FollowPage";
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

function performanceComparison(
  overrides: Partial<FollowPerformanceComparisonResponse> = {},
): FollowPerformanceComparisonResponse {
  return {
    subscriptionId: 1,
    sourceListingId: 42,
    trackingDifferencePct: "0.5",
    points: [
      { asOf: "2026-09-01T00:00:00Z", sourceReturnPct: "3.2", followerReturnPct: "2.7" },
      { asOf: "2026-09-02T00:00:00Z", sourceReturnPct: "3.5", followerReturnPct: "3.0" },
    ],
    ...overrides,
  };
}

function stubClient(overrides: Partial<FollowClient> = {}): FollowClient {
  return {
    listSubscriptions: vi.fn().mockResolvedValue({ items: [], total: 0 } satisfies FollowSubscriptionListResponse),
    createSubscription: vi.fn().mockResolvedValue(subscription()),
    cancelSubscription: vi.fn().mockResolvedValue(undefined),
    getPerformanceComparison: vi.fn().mockResolvedValue(performanceComparison()),
    ...overrides,
  };
}

function renderPage(props: FollowPageProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/follow"]}>
        <Routes>
          <Route path="/follow" element={<FollowPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("FollowPage", () => {
  it("항상 투자자문·일임이 아니라는 고지 문구를 보여준다", async () => {
    renderPage({ followClient: stubClient() });
    expect(await screen.findByTestId("follow-advisory-disclaimer")).toHaveTextContent("투자자문·일임이 아닙니다");
  });

  it("구독이 없으면 빈 상태를 보여준다(negative 1/3)", async () => {
    renderPage({ followClient: stubClient() });
    expect(await screen.findByText("팔로우 중인 전략이 없습니다.")).toBeInTheDocument();
  });

  it("라우트가 아직 없으면(FollowRouteNotImplementedError) 경고를 보여주고 재시도 버튼은 없다(negative 2/3)", async () => {
    const client = stubClient({
      listSubscriptions: vi.fn().mockRejectedValue(new FollowRouteNotImplementedError("follow.subscriptions.base")),
    });
    renderPage({ followClient: client });

    expect(await screen.findByText("팔로우 구독 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("CANCELLED 상태 구독은 해지 버튼을 보여주지 않는다(negative 3/3)", async () => {
    const client = stubClient({
      listSubscriptions: vi.fn().mockResolvedValue({
        items: [subscription({ id: 2, status: "CANCELLED" })],
        total: 1,
      }),
    });
    renderPage({ followClient: client });

    await screen.findByText("리스팅 #42");
    expect(screen.getByText("CANCELLED")).toBeInTheDocument();
    expect(screen.queryByText("해지")).not.toBeInTheDocument();
  });

  it("성과 비교를 펼치면 추적 오차와 포인트를 보여준다", async () => {
    const client = stubClient({
      listSubscriptions: vi.fn().mockResolvedValue({ items: [subscription()], total: 1 }),
    });
    renderPage({ followClient: client });

    fireEvent.click(await screen.findByText("성과 비교"));
    expect(await screen.findByText("0.5%", { exact: false })).toBeInTheDocument();
    expect(client.getPerformanceComparison).toHaveBeenCalledWith(1);
  });

  it("성과 데이터가 없으면 안내 문구를 보여준다(크래시 없음)", async () => {
    const client = stubClient({
      listSubscriptions: vi.fn().mockResolvedValue({ items: [subscription()], total: 1 }),
      getPerformanceComparison: vi.fn().mockResolvedValue(performanceComparison({ points: [] })),
    });
    renderPage({ followClient: client });

    fireEvent.click(await screen.findByText("성과 비교"));
    expect(await screen.findByText("아직 비교할 성과 데이터가 없습니다.")).toBeInTheDocument();
  });

  // 실패주입: SweepResultsPage.test.tsx(DEPTH_BT task-2728) 관용과 동일하게 (1) ApiError가
  // 아닌 진짜 네트워크 실패, (2) buildApiError(실제 HTTP 파싱 경로)로 만든 429/500을 각각
  // 주입해 재시도 가능/불가능 갈래를 실제 분류 파이프라인(routeApiError)으로 태운다.
  it("실패주입 1/3: ApiError가 아닌 진짜 네트워크 오류(fetch 실패)도 크래시 없이 일반 오류 배너로 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({
      listSubscriptions: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    });
    renderPage({ followClient: client });

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("실패주입 2/3: buildApiError로 만든 429 오류는 재시도 버튼을 보여주고 클릭하면 다시 조회한다", async () => {
    const realError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
        trace_id: "trace-follow-429",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const listSubscriptions = vi.fn().mockRejectedValue(realError);
    renderPage({ followClient: stubClient({ listSubscriptions }) });

    expect(await screen.findByText("지원코드: trace-follow-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));

    await waitFor(() => expect(listSubscriptions).toHaveBeenCalledTimes(2));
  });

  it("실패주입 3/3: buildApiError로 만든 500(INTERNAL_ERROR)은 지원코드는 보여주되 재시도 버튼은 없다", async () => {
    const realError = buildApiError(
      500,
      {
        error_code: "INTERNAL_ERROR",
        message: "일시적인 오류가 발생했습니다.",
        trace_id: "trace-follow-500",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    renderPage({ followClient: stubClient({ listSubscriptions: vi.fn().mockRejectedValue(realError) }) });

    expect(await screen.findByText("지원코드: trace-follow-500")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // 게이트적색: 아래 2개는 FollowPage.tsx의 handleCancel 분기(성공 시 invalidate로
  // 목록 재조회, 실패 시 actionError만 세팅하고 목록은 그대로 둔다)를 정확히 겨눈다 —
  // 그 분기를 되돌리면(예: catch에서 재throw하거나 성공 시 invalidate를 지우면) 해당
  // 테스트만 적색이 된다.
  it("게이트적색 1/2: 해지 버튼을 누르면 cancelSubscription을 호출하고 목록을 다시 불러온다", async () => {
    const listSubscriptions = vi
      .fn()
      .mockResolvedValueOnce({ items: [subscription({ id: 7 })], total: 1 })
      .mockResolvedValueOnce({ items: [subscription({ id: 7, status: "CANCELLED" })], total: 1 });
    const cancelSubscription = vi.fn().mockResolvedValue(undefined);
    renderPage({ followClient: stubClient({ listSubscriptions, cancelSubscription }) });

    fireEvent.click(await screen.findByText("해지"));

    await waitFor(() => expect(cancelSubscription).toHaveBeenCalledWith(7));
    await waitFor(() => expect(listSubscriptions).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText("CANCELLED")).toBeInTheDocument());
  });

  it("게이트적색 2/2: 해지가 실패하면 오류 배너를 보여주고 목록은 그대로 남는다(낙관적 삭제 없음)", async () => {
    const listSubscriptions = vi.fn().mockResolvedValue({ items: [subscription({ id: 9 })], total: 1 });
    const cancelSubscription = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage({ followClient: stubClient({ listSubscriptions, cancelSubscription }) });

    fireEvent.click(await screen.findByText("해지"));

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(listSubscriptions).toHaveBeenCalledTimes(1);
    expect(screen.getByText("리스팅 #42")).toBeInTheDocument();
  });

  // 성능: 200건의 구독 목록도 예산 시간 안에 렌더해야 한다(idempotency.test.ts
  // task-2730 DEEPEN 관용과 동일하게 수치 예산으로 못박는다).
  it("성능: 200건의 구독 목록도 예산 시간 안에 렌더한다", async () => {
    const items = Array.from({ length: 200 }, (_, i) => subscription({ id: i + 1, sourceListingId: i + 1 }));
    renderPage({
      followClient: stubClient({ listSubscriptions: vi.fn().mockResolvedValue({ items, total: items.length }) }),
    });

    const startedAt = performance_now();
    await screen.findByText("리스팅 #200");
    const elapsedMs = performance_now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(4000));
    expect(document.querySelectorAll('[data-testid^="follow-subscription-"]')).toHaveLength(200);
  });

  // UX-15 "자문 오인 표현 금지 검수" — 실제 화면이 쓰는 사용자 노출 문자열 전체를
  // findAdvisoryLanguageViolations로 감사한다.
  it("자문 오인 표현 금지 검수: 화면의 사용자 노출 문자열에 금지 표현이 0건이다", () => {
    expect(auditFollowPageAdvisoryLanguage()).toEqual([]);
  });

  it("게이트적색: 감사 대상 문자열에 금지 표현이 섞이면 검출된다(감사 도구 자체 검증)", () => {
    expect(findAdvisoryLanguageViolations("이 전략은 매수를 추천합니다")).toContain("매수를 추천");
  });
});

// window.performance.now는 jsdom에도 있지만, 이름 충돌(로컬 헬퍼 performance())을
// 피하려고 별칭을 둔다.
function performance_now(): number {
  return globalThis.performance.now();
}
