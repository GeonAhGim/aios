import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { ListingDetailPage } from "./ListingDetailPage";

const purchaseMutateAsync = vi.fn();
let reviewsResult: { data: unknown; error: unknown } = {
  data: { reviews: [], reviewCount: 0, averageRating: null },
  error: null,
};

vi.mock("@aios/shared-hooks", () => ({
  useListingReviews: () => reviewsResult,
  usePurchaseListing: () => ({ mutateAsync: purchaseMutateAsync, isPending: false }),
  useSubmitForVerification: () => ({ mutate: vi.fn() }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  purchaseMutateAsync.mockReset();
  reviewsResult = { data: { reviews: [], reviewCount: 0, averageRating: null }, error: null };
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/marketplace/1"]}>
      <Routes>
        <Route path="/marketplace/:listingId" element={<ListingDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

// task-901 §3.3: 구매 실패는 err.message를 직접 노출하지 않고 routeApiError로
// 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만 보여준다.
describe("ListingDetailPage 구매 에러 표시", () => {
  it("negative: POLICY_*(403) 거부는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    purchaseMutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-1", "POLICY_LIVE_BLOCKED"),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "구매하기" }));

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    purchaseMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "구매하기" }));

    await waitFor(() => expect(screen.getByText("구매에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  it("위험등급 불일치(400)는 배너가 아니라 RiskWarningModal로 사유를 보여준다", async () => {
    purchaseMutateAsync.mockRejectedValue(
      new ApiError(400, "회원님의 위험등급(안정형)보다 위험도가 높은 대상입니다.", undefined),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "구매하기" }));

    await waitFor(() =>
      expect(screen.getByText("위험등급 불일치 경고")).toBeInTheDocument(),
    );
    expect(
      screen.getByText("회원님의 위험등급(안정형)보다 위험도가 높은 대상입니다."),
    ).toBeInTheDocument();
  });
});

// spec §3.3 RESOURCE_NOT_FOUND(404)는 재시도 배너가 아니라 NotFoundState로 렌더한다.
describe("ListingDetailPage 리뷰 목록 404", () => {
  it("negative: 리뷰 조회가 RESOURCE_NOT_FOUND(404)면 NotFoundState를 보여준다", () => {
    reviewsResult = {
      data: undefined,
      error: new ApiError(404, "not found", undefined, "RESOURCE_NOT_FOUND"),
    };
    renderPage();

    expect(screen.getByText("리스팅을 찾을 수 없습니다")).toBeInTheDocument();
  });
});
