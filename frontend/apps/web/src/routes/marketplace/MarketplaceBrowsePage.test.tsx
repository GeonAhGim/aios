import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ListingSummary } from "@aios/shared-types";
import { MarketplaceBrowsePage } from "./MarketplaceBrowsePage";

function makeListings(count: number): ListingSummary[] {
  return Array.from({ length: count }, (_, i) => ({
    id: i + 1,
    strategyId: `strategy-${i + 1}`,
    strategyVersion: "1.0.0",
    sellerUserId: "seller-1",
    sellerType: "USER" as const,
    price: null,
    verifiedAt: null,
    sharpeRatio: null,
  }));
}

let allListings: ListingSummary[] = [];

vi.mock("@aios/shared-hooks", () => ({
  useListingSearch: (params: { page?: number; pageSize?: number }) => {
    const page = params.page ?? 1;
    const pageSize = params.pageSize ?? 20;
    const start = (page - 1) * pageSize;
    return {
      data: {
        items: allListings.slice(start, start + pageSize),
        total: allListings.length,
        page,
        pageSize,
      },
      isLoading: false,
    };
  },
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
      <MarketplaceBrowsePage />
      <LocationDisplay />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  allListings = [];
});

// task-323: 마지막 페이지를 넘어선 요청은 derivePageState가 클램프하고,
// MarketplaceBrowsePage는 그 값으로 URL을 되돌려 재요청한다고 커밋 메시지가 주장한다 —
// 실제로 URL과 화면이 클램프된 페이지로 수렴하는지 검증한다.
describe("MarketplaceBrowsePage 페이지네이션", () => {
  it("정상 페이지: 요청한 페이지의 항목만 보여준다", () => {
    allListings = makeListings(45);
    renderAt("/marketplace?page=2&size=20");

    expect(screen.getByText("strategy-21")).toBeInTheDocument();
    expect(screen.getByText("strategy-40")).toBeInTheDocument();
    expect(screen.queryByText("strategy-1")).not.toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("negative: 총 페이지(3)를 넘는 page=999 요청은 마지막 페이지로 클램프되어 URL과 화면이 수렴한다", async () => {
    allListings = makeListings(45);
    expect(() => renderAt("/marketplace?page=999&size=20")).not.toThrow();

    await waitFor(() => {
      expect(screen.getByTestId("location").textContent).toBe(
        "?page=3&size=20",
      );
    });
    expect(screen.getByText("strategy-41")).toBeInTheDocument();
    expect(screen.getByText("strategy-45")).toBeInTheDocument();
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
  });

  it("negative: 리스팅이 0건이면 클램프를 시도하지 않고 빈 상태를 보여준다", () => {
    allListings = [];
    expect(() => renderAt("/marketplace?page=5")).not.toThrow();

    expect(screen.getByText("등록된 리스팅이 없습니다.")).toBeInTheDocument();
  });
});
