import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AiosApiClient, ApiError } from "@aios/api-client";
import type { PurchaseCreateRequest } from "@aios/shared-types";
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
  vi.unstubAllGlobals();
});

// task-1049 §3.7/§9 PLT-15: purchaseMutateAsync를 단순 spy로 두면 컴포넌트가 어떤
// 문자열을 넘겼는지만 확인하는 동어반복이 된다 — 아래 describe는 mutateAsync가
// 실제 AiosApiClient(멱등 헤더 조립 포함)에 위임하도록 해 fetch로 나간 진짜
// Idempotency-Key 헤더값을 단언한다.
const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,128}$/;
const realClient = new AiosApiClient("https://api.example.test", () => null);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function idempotencyKeyOf(fetchMock: ReturnType<typeof vi.fn>): string | null {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return new Headers(init.headers).get("Idempotency-Key");
}

function delegateToRealClient() {
  purchaseMutateAsync.mockImplementation(
    (vars: { listingId: number; body: PurchaseCreateRequest; idempotencyKey: string }) =>
      realClient.purchaseListing(vars.listingId, vars.body, vars.idempotencyKey),
  );
}

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

  // task-1215: PLT-18/19 이후 실제 백엔드는 이 400을 항상 error_code=
  // VALIDATION_INVALID_FIELD(details.fields=[])로 봉투에 담아 보낸다(map_exception,
  // task-1207 발견). 이전 테스트는 errorCode를 비워둬 getApiErrorMessage가
  // err.message를 그대로 반환하는 비현실적 경로만 통과시켰다 — 실제로는 errorCode가
  // EXACT_MESSAGES를 먼저 맞혀 원문을 가리는 바람에 이 모달이 죽은 코드였다.
  it("위험등급 불일치(400, 실제 서버 봉투 VALIDATION_INVALID_FIELD)는 배너가 아니라 RiskWarningModal로 사유를 보여준다", async () => {
    purchaseMutateAsync.mockRejectedValue(
      new ApiError(
        400,
        "회원님의 위험등급(안정형)보다 위험도가 높은 대상입니다.",
        undefined,
        "VALIDATION_INVALID_FIELD",
        undefined,
        { fields: [] },
      ),
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

  // task-1215 회귀: 위험등급 문구를 포함하지 않는 그 외 PurchaseError(예: 리스팅 상태
  // 불일치)는 VALIDATION_INVALID_FIELD(400) + details.fields=[]로 와도 인라인 폼이
  // 뜰 자리가 없어 예전엔 화면이 통째로 무응답이었다(task-1207 발견 1, task-1214가
  // BadRequestNotice에서 고침) — 이 화면의 구매 실패 경로가 실제로 그 폴백까지
  // 이어지는지 잠근다.
  it("negative: 위험등급 문구가 없는 VALIDATION_INVALID_FIELD(400) + 빈 details.fields는 서버 message 배너로 폴백한다(P0 무응답 회귀 방지)", async () => {
    purchaseMutateAsync.mockRejectedValue(
      new ApiError(
        400,
        "구매할 수 없는 리스팅 상태입니다(현재: SUSPENDED).",
        undefined,
        "VALIDATION_INVALID_FIELD",
        undefined,
        { fields: [] },
      ),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "구매하기" }));

    await waitFor(() =>
      expect(
        screen.getByText("구매할 수 없는 리스팅 상태입니다(현재: SUSPENDED)."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("위험등급 불일치 경고")).not.toBeInTheDocument();
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

describe("ListingDetailPage 구매 Idempotency-Key(§3.7) 실제 헤더 검증", () => {
  it("구매하면 실제 요청 헤더에 규격(16~128자, [A-Za-z0-9_-])을 만족하는 Idempotency-Key를 싣는다", async () => {
    const fetchMock = stubFetch({
      purchase_id: 1,
      status: "PENDING",
      risk_warning: false,
      risk_warning_reason: null,
    });
    delegateToRealClient();
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "구매하기" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(idempotencyKeyOf(fetchMock)).toMatch(IDEMPOTENCY_KEY_RE);
  });

  it("negative: 409 STATE_CONCURRENCY_CONFLICT 후 재구매하면 새 Idempotency-Key로 다시 보낸다", async () => {
    const firstFetch = stubFetch(
      {
        error_code: "STATE_CONCURRENCY_CONFLICT",
        message: "충돌",
        details: {},
        trace_id: "t-listing-409",
        retry_after_seconds: null,
      },
      409,
    );
    delegateToRealClient();
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "구매하기" }));
    await waitFor(() =>
      expect(
        screen.getByText("다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    const firstKey = idempotencyKeyOf(firstFetch);
    expect(firstKey).toMatch(IDEMPOTENCY_KEY_RE);

    const secondFetch = stubFetch({
      purchase_id: 2,
      status: "PENDING",
      risk_warning: false,
      risk_warning_reason: null,
    });
    fireEvent.click(screen.getByRole("button", { name: "구매하기" }));
    await waitFor(() => expect(secondFetch).toHaveBeenCalledTimes(1));
    expect(idempotencyKeyOf(secondFetch)).not.toBe(firstKey);
  });

  it("negative: 429 RATE_LIMIT_EXCEEDED는 매핑 문구를 보여주고 서버 원문은 노출하지 않으며, 재구매는 새 Idempotency-Key로 나간다", async () => {
    const firstFetch = stubFetch(
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "raw rate limit detail",
        details: {},
        trace_id: "t-listing-429",
        retry_after_seconds: 1,
      },
      429,
    );
    delegateToRealClient();
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "구매하기" }));
    await waitFor(() =>
      expect(
        screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw rate limit detail")).not.toBeInTheDocument();
    const firstKey = idempotencyKeyOf(firstFetch);

    const secondFetch = stubFetch({
      purchase_id: 3,
      status: "PENDING",
      risk_warning: false,
      risk_warning_reason: null,
    });
    fireEvent.click(screen.getByRole("button", { name: "구매하기" }));
    await waitFor(() => expect(secondFetch).toHaveBeenCalledTimes(1));
    expect(idempotencyKeyOf(secondFetch)).not.toBe(firstKey);
  });
});
