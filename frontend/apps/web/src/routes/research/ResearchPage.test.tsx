import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildApiError,
  ResearchDataRouteNotImplementedError,
  type ResearchDataClient,
} from "@aios/api-client";
import type { ResearchSearchResponse, ResearchSourceStatusView } from "@aios/shared-types";
import { ResearchPage, type ResearchPageProps } from "./ResearchPage";
import { perfBudgetMs } from "../../test/perfBudget";
// i18n/index.ts(task-2685)의 initI18n()이 모듈 로드 시 1회 부수효과로 실행된다 —
// ScreenerPage.test.tsx(task-2692)와 동일 사유로, t()가 키 문자열 그대로를
// 반환하지 않도록 먼저 로드해 둔다.
import "../../i18n";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function searchResponse(overrides: Partial<ResearchSearchResponse> = {}): ResearchSearchResponse {
  return {
    items: [
      {
        itemId: "item-1",
        sourceId: "opendart",
        kind: "filing",
        title: "삼성전자 분기보고서",
        url: "https://dart.fss.or.kr/item-1",
        publishedAt: "2026-09-01T00:00:00+00:00",
        knownAt: "2026-09-01T00:00:00+00:00",
        instrumentId: "inst-1",
        unmappedReason: null,
      },
    ],
    total: 1,
    truncated: false,
    ...overrides,
  };
}

function sourcesResponse(overrides: Partial<ResearchSourceStatusView>[] = []): ResearchSourceStatusView[] {
  if (overrides.length > 0) {
    return overrides.map((o, i) => ({
      sourceId: `source-${i}`,
      publisher: "금감원 OpenDART",
      redistribution: "store_full",
      licenseRef: "opendart-tos",
      rateLimit: 1000,
      coverage: "2015-01-01~present",
      ...o,
    }));
  }
  return [
    {
      sourceId: "opendart",
      publisher: "금감원 OpenDART",
      redistribution: "store_full",
      licenseRef: "opendart-tos",
      rateLimit: 1000,
      coverage: "2015-01-01~present",
    },
  ];
}

function stubClient(overrides: Partial<ResearchDataClient> = {}): ResearchDataClient {
  return {
    search: vi.fn().mockResolvedValue(searchResponse()),
    listSources: vi.fn().mockResolvedValue(sourcesResponse()),
    ...overrides,
  };
}

function renderPage(props: ResearchPageProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/research"]}>
        <Routes>
          <Route path="/research" element={<ResearchPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fillQuery(value: string) {
  fireEvent.change(screen.getByTestId("research-query"), { target: { value } });
}

function run() {
  fireEvent.click(screen.getByTestId("research-search-run"));
}

describe("ResearchPage", () => {
  it("실행 전에는 안내만 보여주고 검색 API를 호출하지 않는다(소스 상태는 즉시 조회)", async () => {
    const client = stubClient();
    renderPage({ researchDataClient: client });

    expect(screen.getByText("검색어를 입력하고 검색하세요.")).toBeInTheDocument();
    expect(client.search).not.toHaveBeenCalled();
    await waitFor(() => expect(client.listSources).toHaveBeenCalledTimes(1));
  });

  it("negative 1/3: 검색어가 비어 있으면 검증 오류를 보여주고 검색 API를 호출하지 않는다", () => {
    const client = stubClient();
    renderPage({ researchDataClient: client });

    run();

    expect(screen.getByTestId("research-validation-alert")).toHaveTextContent("검색어를 입력하세요.");
    expect(client.search).not.toHaveBeenCalled();
  });

  it("negative 2/3: 검색 결과가 없으면 빈 상태를 보여준다(크래시 없음)", async () => {
    const client = stubClient({ search: vi.fn().mockResolvedValue(searchResponse({ items: [], total: 0 })) });
    renderPage({ researchDataClient: client });

    fillQuery("삼성전자");
    run();

    expect(await screen.findByText("조건에 맞는 항목이 없습니다.")).toBeInTheDocument();
  });

  it("negative 3/3: 라우트가 아직 없으면(ResearchDataRouteNotImplementedError) 경고를 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({
      search: vi.fn().mockRejectedValue(new ResearchDataRouteNotImplementedError("researchData.search")),
    });
    renderPage({ researchDataClient: client });

    fillQuery("삼성전자");
    run();

    expect(await screen.findByText("리서치 데이터 API가 아직 제공되지 않습니다.")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  it("검색 실행 시 선택한 종류(kinds)·종목·기준시각을 그대로 실어 보낸다", async () => {
    const search = vi.fn().mockResolvedValue(searchResponse());
    renderPage({ researchDataClient: stubClient({ search }) });

    fillQuery("삼성전자");
    fireEvent.click(screen.getByTestId("research-kind-filing"));
    fireEvent.change(screen.getByTestId("research-instrument"), { target: { value: "inst-1" } });
    fireEvent.change(screen.getByTestId("research-asof"), { target: { value: "2026-09-01T00:00:00Z" } });
    run();

    await waitFor(() => expect(search).toHaveBeenCalledTimes(1));
    expect(search).toHaveBeenCalledWith({
      query: "삼성전자",
      kinds: ["filing"],
      instrumentId: "inst-1",
      asOf: "2026-09-01T00:00:00Z",
    });
  });

  it("종목 연결이 있으면 차트로 가는 링크를, 미매핑이면 사유 배지를 보여준다", async () => {
    const client = stubClient({
      search: vi.fn().mockResolvedValue(
        searchResponse({
          items: [
            {
              itemId: "item-mapped",
              sourceId: "opendart",
              kind: "filing",
              title: "매핑됨",
              url: "https://example.com/1",
              publishedAt: "2026-09-01T00:00:00+00:00",
              knownAt: "2026-09-01T00:00:00+00:00",
              instrumentId: "inst-9",
              unmappedReason: null,
            },
            {
              itemId: "item-unmapped",
              sourceId: "gdelt",
              kind: "news",
              title: "미매핑됨",
              url: "https://example.com/2",
              publishedAt: "2026-09-01T00:00:00+00:00",
              knownAt: "2026-09-01T00:00:00+00:00",
              instrumentId: null,
              unmappedReason: "not_found",
            },
          ],
        }),
      ),
    });
    renderPage({ researchDataClient: client });

    fillQuery("검색");
    run();

    const link = (await screen.findByText("차트에서 보기")).closest("a");
    expect(link).toHaveAttribute("href", "/chart?instrument_id=inst-9");
    expect(screen.getByTestId("research-item-item-unmapped-unmapped")).toHaveTextContent("미매핑(종목 없음)");
  });

  it("소스 상태 패널은 발행처·커버리지·재배포 정책을 보여준다", async () => {
    const client = stubClient({
      listSources: vi.fn().mockResolvedValue(
        sourcesResponse([{ sourceId: "gdelt", publisher: "GDELT", redistribution: "link_only", coverage: "글로벌 뉴스" }]),
      ),
    });
    renderPage({ researchDataClient: client });

    expect(await screen.findByTestId("research-source-gdelt")).toHaveTextContent("GDELT");
    expect(screen.getByTestId("research-source-gdelt")).toHaveTextContent("글로벌 뉴스");
    expect(screen.getByTestId("research-source-gdelt")).toHaveTextContent("링크만");
  });

  it("실패주입 1/3: ApiError가 아닌 진짜 네트워크 오류(fetch 실패)도 크래시 없이 일반 오류 배너로 보여주고 재시도 버튼은 없다", async () => {
    const client = stubClient({ search: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")) });
    renderPage({ researchDataClient: client });

    fillQuery("삼성전자");
    run();

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // RD-17은 RD_RATE_LIMITED(spec §3) 자체를 classifyRetry에 새로 등록하는
  // 리프가 아니다(routeApiError는 errorCode 화이트리스트 기반이라 미등록
  // RD_* 코드는 재시도 불가로 수렴한다, errorRouting.ts 우선순위 규칙 참조) —
  // 이 배너가 "이미 등록된" 재시도 가능 코드(RATE_LIMIT_EXCEEDED, ScreenerPage.
  // test.tsx와 동일)에 정확히 반응하는지만 검증한다.
  it("실패주입 2/3: buildApiError로 만든 429(RATE_LIMIT_EXCEEDED)는 재시도 버튼을 보여주고 클릭하면 다시 조회한다", async () => {
    const realError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
        trace_id: "trace-research-429",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    const search = vi.fn().mockRejectedValue(realError);
    renderPage({ researchDataClient: stubClient({ search }) });

    fillQuery("삼성전자");
    run();

    expect(await screen.findByText("지원코드: trace-research-429")).toBeInTheDocument();
    fireEvent.click(screen.getByText("다시 시도"));

    await waitFor(() => expect(search).toHaveBeenCalledTimes(2));
  });

  it("실패주입 3/3: buildApiError로 만든 403(RD_REDISTRIBUTION_DENIED)은 지원코드는 보여주되 재시도 버튼은 없다", async () => {
    const realError = buildApiError(
      403,
      {
        error_code: "RD_REDISTRIBUTION_DENIED",
        message: "이 소스는 재배포가 허용되지 않습니다.",
        trace_id: "trace-research-403",
        retry_after_seconds: null,
        details: {},
      },
      undefined,
      undefined,
    );
    renderPage({ researchDataClient: stubClient({ search: vi.fn().mockRejectedValue(realError) }) });

    fillQuery("삼성전자");
    run();

    expect(await screen.findByText("지원코드: trace-research-403")).toBeInTheDocument();
    expect(screen.queryByText("다시 시도")).not.toBeInTheDocument();
  });

  // 게이트적색: 아래는 ResearchPage.tsx의 특정 분기를 정확히 겨눈다 — 그 분기를
  // 되돌리면 이 테스트만 적색이 된다(buildInput의 trim+undefined 정규화 분기).
  it("게이트적색: 종목·기준시각을 비운 채 검색하면 undefined로 정규화해 보낸다(공백 문자열 누수 금지)", async () => {
    const search = vi.fn().mockResolvedValue(searchResponse());
    renderPage({ researchDataClient: stubClient({ search }) });

    fillQuery("삼성전자");
    fireEvent.change(screen.getByTestId("research-instrument"), { target: { value: "   " } });
    fireEvent.change(screen.getByTestId("research-asof"), { target: { value: "" } });
    run();

    await waitFor(() => expect(search).toHaveBeenCalledTimes(1));
    expect(search.mock.calls[0][0]).toEqual({
      query: "삼성전자",
      kinds: [],
      instrumentId: undefined,
      asOf: undefined,
    });
  });

  // 성능: 200건의 결과 행도 예산 시간 안에 렌더해야 한다(ScreenerPage.test.tsx
  // task-2692 관용과 동일하게 수치 예산으로 못박는다).
  it("성능: 200건의 결과 행도 예산 시간 안에 렌더한다", async () => {
    const items = Array.from({ length: 200 }, (_, i) => ({
      itemId: `item-${i}`,
      sourceId: "opendart",
      kind: "filing" as const,
      title: `제목-${i}`,
      url: `https://example.com/${i}`,
      publishedAt: "2026-09-01T00:00:00+00:00",
      knownAt: "2026-09-01T00:00:00+00:00",
      instrumentId: `inst-${i}`,
      unmappedReason: null,
    }));
    const client = stubClient({
      search: vi.fn().mockResolvedValue(searchResponse({ items, total: items.length })),
    });
    renderPage({ researchDataClient: client });

    fillQuery("삼성전자");

    const startedAt = performance.now();
    run();
    await screen.findByTestId("research-item-item-199");
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(4000));
    expect(document.querySelectorAll('[data-testid^="research-item-"]')).toHaveLength(200);
  });
});
