import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { MandatesPage } from "./MandatesPage";

const createDraftMutate = vi.fn();
const proposeAmendmentMutate = vi.fn();
const activateMutate = vi.fn();
const pauseMutate = vi.fn();
const resumeMutate = vi.fn();
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
  useCreateMandateDraft: () => ({ mutate: createDraftMutate, isPending: false }),
  useProposeMandateAmendment: () => ({ mutate: proposeAmendmentMutate, isPending: false }),
  useActivateMandateRevision: () => ({ mutate: activateMutate, isPending: false }),
  usePauseMandate: () => ({ mutate: pauseMutate, isPending: false }),
  useResumeMandate: () => ({ mutate: resumeMutate, isPending: false }),
  useEvaluateMandatePolicy: () => ({ mutate: evaluateMutate, isPending: false }),
  useMe: () => ({ data: { email: "user@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  createDraftMutate.mockReset();
  proposeAmendmentMutate.mockReset();
  activateMutate.mockReset();
  pauseMutate.mockReset();
  resumeMutate.mockReset();
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
      <MandatesPage />
    </MemoryRouter>,
  );
}

describe("MandatesPage — 활성/대기 리비전 구분 렌더", () => {
  it("negative(fail-closed, DoD a): activeRevision이 없으면 '제한 없음'이 아니라 '위임장 미설정(주문 차단)'을 보여준다", () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: null, pendingRevision: null },
    };
    renderPage();

    expect(screen.getByText("위임장 미설정(주문 차단)")).toBeInTheDocument();
    expect(screen.queryByText("제한 없음")).not.toBeInTheDocument();
  });

  it("activeRevision이 있으면 상태 배지와 규칙 요약을 보여준다(ACTIVE는 일시정지 버튼)", () => {
    renderPage();

    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("총 노출 한도: 50%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "일시정지" })).toBeInTheDocument();
  });

  it("pendingRevision이 있으면 대기 중 개정안 카드에 활성화 버튼을 보여주고 초안/개정안 폼은 숨긴다", () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: ACTIVE_REVISION, pendingRevision: PENDING_REVISION },
    };
    renderPage();

    expect(screen.getByRole("button", { name: "활성화" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "개정안 제안" })).not.toBeInTheDocument();
  });

  it("pendingRevision이 없고 activeRevision이 있으면 '개정안 제안' 폼을 보여준다", () => {
    renderPage();
    expect(screen.getByRole("button", { name: "개정안 제안" })).toBeInTheDocument();
  });

  it("pendingRevision도 activeRevision도 없으면 '초안 작성' 폼을 보여준다", () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: null, pendingRevision: null },
    };
    renderPage();
    expect(screen.getByRole("button", { name: "초안 작성" })).toBeInTheDocument();
  });

  it("negative: 상태 조회가 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    statusResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-m-1"),
      refetch: refetchStatus,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("위임장 미설정(주문 차단)")).not.toBeInTheDocument();
  });
});

describe("MandatesPage — activate(revisions/{id}:activate) 실패 표시", () => {
  function withPending() {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: ACTIVE_REVISION, pendingRevision: PENDING_REVISION },
    };
  }

  it("negative(DoD b): CM-5 작성자=승인자(400 VALIDATION_INVALID_FIELD, details.fields 없음)는 버튼을 비활성화하지 않고 거부 사유 배너를 보여준다", async () => {
    withPending();
    activateMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(
          400,
          "proposer(p1)와 approver(p1)가 동일합니다 — 작성자는 자신이 제안한 개정을 직접 활성화할 수 없습니다(CM-A3).",
          "trace-cm5",
          "VALIDATION_INVALID_FIELD",
        ),
      );
    });
    renderPage();

    const activateButton = screen.getByRole("button", { name: "활성화" });
    expect(activateButton).not.toBeDisabled();
    fireEvent.click(activateButton);

    await waitFor(() =>
      expect(
        screen.getByText(
          "proposer(p1)와 approver(p1)가 동일합니다 — 작성자는 자신이 제안한 개정을 직접 활성화할 수 없습니다(CM-A3).",
        ),
      ).toBeInTheDocument(),
    );
    expect(activateButton).not.toBeDisabled();
  });

  it("negative(DoD c): 동시 activate 경합(409 STATE_CONCURRENCY_CONFLICT)은 메시지를 보여주고 즉시 재조회한다", async () => {
    withPending();
    activateMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(409, "다른 요청이 먼저 처리했습니다.", "trace-conflict", "STATE_CONCURRENCY_CONFLICT"),
      );
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "활성화" }));

    await waitFor(() =>
      expect(screen.getByText("다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
    expect(refetchStatus).toHaveBeenCalled();
  });

  it("activate.mutate는 pendingRevision.id를 revisionId로 넘긴다", () => {
    withPending();
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "활성화" }));

    expect(activateMutate).toHaveBeenCalledWith({ revisionId: "rev-pending" }, expect.anything());
  });
});

describe("MandatesPage — 정책 평가(policy:evaluate) 빈 결과 vs 평가 실패 구분(DoD d)", () => {
  it("positive: 위반이 없으면(reasonCodes=[]) '위반 규칙 없음'을 보여준다(평가 실패와 다른 분기)", async () => {
    evaluateMutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({
        id: "d1",
        tenantId: "tenant-1",
        bundleId: "b1",
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
  });

  it("위반이 있으면 사유 코드 목록을 보여준다", async () => {
    evaluateMutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({
        id: "d2",
        tenantId: "tenant-1",
        bundleId: "b1",
        commandType: "ORDER_SUBMIT",
        outcome: "DENY",
        reasonCodes: ["RISK_MAX_POSITION_EXCEEDED"],
        obligations: [],
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
  });

  it("negative: 평가 자체가 실패하면(500) '위반 규칙 없음'이 아니라 ErrorMessage를 보여준다", async () => {
    evaluateMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(500, "일시적인 오류입니다.", "trace-eval-1"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "평가" }));

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("위반 규칙 없음.")).not.toBeInTheDocument();
  });
});
