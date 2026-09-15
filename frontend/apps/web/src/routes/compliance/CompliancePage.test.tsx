import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { CompliancePage } from "./CompliancePage";

const evaluateMutate = vi.fn();
const refetchStatus = vi.fn();

const ACTIVE_REVISION = {
  id: "rev-active",
  mandateId: "mandate-1",
  revisionNo: 1,
  state: "ACTIVE",
  maxTotalExposurePct: 50,
  maxSingleInstrumentPct: 20,
  minCashBufferPct: 5,
  maxDailyLossPct: 3,
  allowedAutonomy: "PAPER",
  forbiddenAssets: [],
  revisionHash: "a".repeat(64),
  coolingOffStartedAt: null,
  createdAt: "2026-09-09T00:00:00Z",
  activatedAt: "2026-09-09T00:00:00Z",
  schemaVersion: "v1",
};

const PENDING_REVISION = { ...ACTIVE_REVISION, id: "rev-pending", revisionNo: 2, state: "PROPOSED" };

let statusResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = {
  data: { tenantId: "tenant-1", activeRevision: ACTIVE_REVISION, pendingRevision: null },
  isLoading: false,
  isError: false,
  error: null,
  refetch: refetchStatus,
};

vi.mock("@aios/shared-hooks", () => ({
  useMandateStatus: () => statusResult,
  useEvaluateMandatePolicy: () => ({ mutate: evaluateMutate, isPending: false }),
  useMe: () => ({ data: { email: "user@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  evaluateMutate.mockReset();
  refetchStatus.mockReset();
  statusResult = {
    data: { tenantId: "tenant-1", activeRevision: ACTIVE_REVISION, pendingRevision: null },
    isLoading: false,
    isError: false,
    error: null,
    refetch: refetchStatus,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <CompliancePage />
    </MemoryRouter>,
  );
}

describe("CompliancePage — 로딩/오류/빈 상태", () => {
  it("로딩 중이면 LoadingState를 보여준다", () => {
    statusResult = { data: undefined, isLoading: true, isError: false, error: null, refetch: refetchStatus };
    renderPage();

    expect(screen.getByText("불러오는 중...")).toBeInTheDocument();
  });

  it("negative: 상태 조회가 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    statusResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-c-1"),
      refetch: refetchStatus,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("위임장 미설정(주문 차단)")).not.toBeInTheDocument();
  });

  it("negative(fail-closed): activeRevision이 없으면 '제한 없음'이 아니라 '위임장 미설정(주문 차단)'을 보여준다", () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: null, pendingRevision: null },
    };
    renderPage();

    expect(screen.getByText("위임장 미설정(주문 차단)")).toBeInTheDocument();
    expect(screen.queryByText("제한 없음")).not.toBeInTheDocument();
  });

  it("activeRevision이 있으면 mandate 상태 카드에 규칙 요약을 보여준다", () => {
    renderPage();

    expect(screen.getByText("mandate 상태")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("총 노출 한도: 50%")).toBeInTheDocument();
  });

  it("pendingRevision이 있으면 대기 중 개정안 요약도 함께 보여준다", () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: ACTIVE_REVISION, pendingRevision: PENDING_REVISION },
    };
    renderPage();

    expect(screen.getByText("대기 중 개정안")).toBeInTheDocument();
    expect(screen.getByText("PROPOSED")).toBeInTheDocument();
  });
});

describe("CompliancePage — 판정 조회(policy:evaluate) 빈 결과 vs 평가 실패 구분", () => {
  it("positive: 위반이 없으면(reasonCodes=[]) '위반 규칙 없음'과 설명(번들·시각)을 보여준다", async () => {
    evaluateMutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({
        id: "d1",
        tenantId: "tenant-1",
        bundleId: "bundle-1",
        commandType: "ORDER_SUBMIT",
        outcome: "ALLOW",
        reasonCodes: [],
        obligations: [],
        evaluatedAt: "2026-09-09T00:00:00Z",
        expiresAt: "2026-09-09T00:00:30Z",
        schemaVersion: "v1",
      });
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "평가" }));

    await waitFor(() => expect(screen.getByText("위반 규칙 없음.")).toBeInTheDocument());
    expect(screen.getByText("ALLOW")).toBeInTheDocument();
    expect(screen.getByText("bundle-1")).toBeInTheDocument();
    expect(screen.getByText("2026-09-09T00:00:30Z")).toBeInTheDocument();
  });

  it("위반이 있으면 사유 코드 목록을 판정 목록으로 보여준다", async () => {
    evaluateMutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({
        id: "d2",
        tenantId: "tenant-1",
        bundleId: "bundle-1",
        commandType: "ORDER_SUBMIT",
        outcome: "DENY",
        reasonCodes: ["RISK_MAX_POSITION_EXCEEDED"],
        obligations: ["MANUAL_REVIEW"],
        evaluatedAt: "2026-09-09T00:00:00Z",
        expiresAt: null,
        schemaVersion: "v1",
      });
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "평가" }));

    await waitFor(() =>
      expect(screen.getByText("허용된 최대 포지션 한도를 초과하여 거부되었습니다.")).toBeInTheDocument(),
    );
    expect(screen.getByText("만료 없음")).toBeInTheDocument();
    expect(screen.getByText("MANUAL_REVIEW")).toBeInTheDocument();
  });

  it("negative: 평가 자체가 실패하면(500) '위반 규칙 없음'이 아니라 ErrorMessage를 보여준다", async () => {
    evaluateMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(500, "일시적인 오류입니다.", "trace-eval-c-1"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "평가" }));

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("위반 규칙 없음.")).not.toBeInTheDocument();
  });
});
