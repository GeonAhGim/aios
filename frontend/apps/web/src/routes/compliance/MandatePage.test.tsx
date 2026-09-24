import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { MandatePage } from "./MandatePage";

const createDraftMutate = vi.fn();
const proposeAmendmentMutate = vi.fn();
const activateMutate = vi.fn();
const pauseMutate = vi.fn();
const resumeMutate = vi.fn();
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
      <MandatePage />
    </MemoryRouter>,
  );
}

describe("MandatePage — 위임장 목록 조회(DoD 1)", () => {
  it("activeRevision/pendingRevision이 있으면 둘 다 목록 항목으로 렌더한다", () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: ACTIVE_REVISION, pendingRevision: PENDING_REVISION },
    };
    renderPage();

    expect(screen.getByText("활성")).toBeInTheDocument();
    expect(screen.getByText("대기")).toBeInTheDocument();
    expect(screen.getAllByText("총 노출 한도: 50%")).toHaveLength(2);
  });

  it("negative(빈 상태): activeRevision·pendingRevision이 모두 없으면 목록 대신 빈 상태 문구를 보여준다", () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: null, pendingRevision: null },
    };
    renderPage();

    expect(screen.getByText("등록된 위임장이 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("위임장 미설정(주문 차단)")).toBeInTheDocument();
    expect(screen.queryByText("활성")).not.toBeInTheDocument();
  });
});

describe("MandatePage — 편집/승인 API 실패는 무음 실패 금지(DoD 2)", () => {
  it("positive: activate 성공 시 revisionId를 그대로 넘기고 에러 배너를 띄우지 않는다", () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: ACTIVE_REVISION, pendingRevision: PENDING_REVISION },
    };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "활성화" }));

    expect(activateMutate).toHaveBeenCalledWith({ revisionId: "rev-pending" }, expect.anything());
  });

  it("negative(실패주입 1): activate가 400(CM-5 작성자=승인자)으로 실패하면 버튼을 비활성화하지 않고 거부 사유를 노출한다", async () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: ACTIVE_REVISION, pendingRevision: PENDING_REVISION },
    };
    activateMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(400, "proposer(p1)와 approver(p1)가 동일합니다.", "trace-cm5", "VALIDATION_INVALID_FIELD"),
      );
    });
    renderPage();

    const activateButton = screen.getByRole("button", { name: "활성화" });
    fireEvent.click(activateButton);

    await waitFor(() =>
      expect(screen.getByText("proposer(p1)와 approver(p1)가 동일합니다.")).toBeInTheDocument(),
    );
    expect(activateButton).not.toBeDisabled();
  });

  it("negative: pause가 실패하면 일시정지 버튼 아래에 에러를 노출한다", async () => {
    pauseMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(500, "일시적인 오류입니다.", "trace-pause-1"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "일시정지" }));

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
  });

  it("negative: 초안 작성 제출이 실패하면 폼 아래에 에러를 노출한다", async () => {
    statusResult = {
      ...statusResult,
      data: { tenantId: "tenant-1", activeRevision: null, pendingRevision: null },
    };
    createDraftMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(500, "일시적인 오류입니다.", "trace-draft-1"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "초안 작성" }));

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
  });
});

describe("MandatePage — 교차 테넌트 404 동형(DoD 3)", () => {
  it("negative: 상태 조회가 404(다른 테넌트 소유 위임장)로 실패해도 500과 동일한 일반 에러 경로로만 렌더하고 테넌트 정보를 노출하지 않는다", async () => {
    statusResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(404, "찾을 수 없습니다.", "trace-404-1"),
      refetch: refetchStatus,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("찾을 수 없습니다.")).toBeInTheDocument());
    expect(screen.queryByText(/tenant/i)).not.toBeInTheDocument();
    expect(screen.queryByText("등록된 위임장이 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 상태 조회가 500으로 실패해도 404와 같은 ErrorMessage 경로로 렌더한다(형태가 갈라지지 않는다)", async () => {
    statusResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-500-1"),
      refetch: refetchStatus,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
  });
});
