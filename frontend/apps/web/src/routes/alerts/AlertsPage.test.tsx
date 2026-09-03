import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PriceAlert } from "@aios/shared-types";
import { AlertsPage } from "./AlertsPage";

function makeAlerts(count: number): PriceAlert[] {
  return Array.from({ length: count }, (_, i) => ({
    id: i + 1,
    userId: "u1",
    exchange: "bitget",
    symbol: `SYM${i + 1}`,
    timeframe: "1h",
    indicator: "RSI",
    params: {},
    operator: "<",
    threshold: 30,
    status: "ACTIVE" as const,
    createdAt: "2026-01-01T00:00:00Z",
    triggeredAt: null,
    triggeredValue: null,
  }));
}

let alerts: PriceAlert[] = [];

vi.mock("@aios/shared-hooks", () => ({
  useMyAlerts: () => ({ data: alerts, isLoading: false }),
  useIndicators: () => ({ data: { indicators: ["RSI"] } }),
  useCreateAlert: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCancelAlert: () => ({ mutate: vi.fn(), isPending: false }),
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
}));

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.search}</div>;
}

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AlertsPage />
      <LocationDisplay />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  alerts = [];
});

// task-323: listMyAlerts()는 봉투 미적용 레거시 배열 응답이라 서버 페이지네이션이
// 없다 — derivePageState로 클라이언트에서 자른 결과가 실제로 올바른 구간인지 검증한다.
describe("AlertsPage 페이지네이션", () => {
  it("page=2 요청 시 11~20번째 알림만 보여준다", () => {
    alerts = makeAlerts(25);
    renderAt("/alerts?page=2");

    expect(screen.getByText(/SYM11/)).toBeInTheDocument();
    expect(screen.getByText(/SYM20/)).toBeInTheDocument();
    expect(screen.queryByText(/SYM1 /)).not.toBeInTheDocument();
    expect(screen.queryByText(/SYM21/)).not.toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("다음 클릭 시 21~25번째(마지막 페이지)만 보여주고 다음 버튼이 비활성화된다", () => {
    alerts = makeAlerts(25);
    renderAt("/alerts?page=2");

    fireEvent.click(screen.getByRole("button", { name: "다음" }));

    expect(screen.getByText(/SYM21/)).toBeInTheDocument();
    expect(screen.getByText(/SYM25/)).toBeInTheDocument();
    expect(screen.queryByText(/SYM11/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  });

  it("negative: 마지막 페이지를 넘는 page 쿼리(page=999)도 예외 없이 클램프된 마지막 페이지를 보여준다", () => {
    alerts = makeAlerts(25);
    expect(() => renderAt("/alerts?page=999")).not.toThrow();

    expect(screen.getByText("3 / 3")).toBeInTheDocument();
    expect(screen.getByText(/SYM21/)).toBeInTheDocument();
    expect(screen.getByText(/SYM25/)).toBeInTheDocument();
  });

  it("negative: page 쿼리가 숫자가 아니어도(page=abc) 1페이지로 폴백해 렌더링한다", () => {
    alerts = makeAlerts(25);
    expect(() => renderAt("/alerts?page=abc")).not.toThrow();

    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    expect(screen.getByText(/SYM1 /)).toBeInTheDocument();
  });
});
