import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_INSTRUMENTS } from "./demoDataset";
import { DemoChartPage } from "./DemoChartPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

// lightweight-charts는 canvas·matchMedia 등 jsdom이 지원하지 않는 브라우저 API에
// 의존한다(CandlesPage.test.tsx와 동일 관용) — CandlestickChart만 stub으로 바꾼다.
vi.mock("@aios/ui-web", async () => {
  const actual = await vi.importActual<typeof import("@aios/ui-web")>("@aios/ui-web");
  return {
    ...actual,
    CandlestickChart: ({ data }: { data: unknown[] }) => (
      <div data-testid="candlestick-chart">캔들 {data.length}개</div>
    ),
  };
});

afterEach(cleanup);

function renderAt(instrumentId: string) {
  render(
    <MemoryRouter initialEntries={[`/onboarding/demo/${instrumentId}`]}>
      <Routes>
        <Route path="/onboarding/demo/:instrumentId" element={<DemoChartPage />} />
        <Route path="/onboarding/demo" element={<div>데모 목록 페이지</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DemoChartPage", () => {
  it("유효한 데모 종목이면 365개 일봉 차트를 그린다", () => {
    renderAt(DEMO_INSTRUMENTS[0].id);
    expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("365개");
  });

  it("백테스트 실행 버튼을 누르면 로딩 상태를 거쳐 요약이 표시된다", async () => {
    renderAt(DEMO_INSTRUMENTS[0].id);
    fireEvent.click(screen.getByTestId("demo-backtest-run"));

    await waitFor(() => expect(screen.getByTestId("demo-backtest-summary")).toBeInTheDocument());
  });

  it("negative: 알 수 없는 데모 종목이면 차트 대신 안내와 목록 복귀 링크를 보여준다", () => {
    renderAt("NOT-A-DEMO-SYMBOL");
    expect(screen.queryByTestId("candlestick-chart")).not.toBeInTheDocument();
    expect(screen.getByText("알 수 없는 데모 종목입니다.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "데모 종목 목록으로" }));
  });

  it("negative: 백테스트를 실행하기 전에는 요약 영역이 렌더되지 않는다", () => {
    renderAt(DEMO_INSTRUMENTS[1].id);
    expect(screen.queryByTestId("demo-backtest-summary")).not.toBeInTheDocument();
  });
});
