import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { EvidenceChainPage } from "./EvidenceChainPage";

const verifyMutate = vi.fn();
const refetchTimeline = vi.fn();

const EVENT = {
  id: "ev-1",
  tenantId: "tenant-1",
  sequenceNo: 5,
  aggregateType: "MANDATE",
  aggregateId: "m-1",
  aggregateRevision: 2,
  action: "ACTIVATE",
  outcome: "SUCCESS",
  actorSubjectId: "user-1",
  traceId: "trace-ev-1",
  payloadHash: "hash1",
  payload: {},
  classification: "INTERNAL",
  previousHash: "hash0",
  eventHash: "hash1",
  occurredAt: "2026-09-09T00:00:00Z",
  schemaVersion: "v1",
};

let timelineResult: {
  data: unknown;
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = {
  data: { items: [EVENT], nextCursor: null, asOf: "2026-09-09T00:00:00Z" },
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null,
  refetch: refetchTimeline,
};

let verifyResult: {
  mutate: (tenantId?: string) => void;
  isPending: boolean;
  isSuccess: boolean;
  isError: boolean;
  error: unknown;
  data: unknown;
} = {
  mutate: verifyMutate,
  isPending: false,
  isSuccess: false,
  isError: false,
  error: null,
  data: undefined,
};

vi.mock("@aios/shared-hooks", () => ({
  useAuditTimeline: () => timelineResult,
  useVerifyAuditChain: () => verifyResult,
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  verifyMutate.mockReset();
  refetchTimeline.mockReset();
  timelineResult = {
    data: { items: [EVENT], nextCursor: null, asOf: "2026-09-09T00:00:00Z" },
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: refetchTimeline,
  };
  verifyResult = {
    mutate: verifyMutate,
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null,
    data: undefined,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <EvidenceChainPage />
    </MemoryRouter>,
  );
}

function clickVerify() {
  fireEvent.click(screen.getByRole("button", { name: "체인 검증" }));
}

describe("EvidenceChainPage 타임라인 조회 실패/빈 상태 표시", () => {
  it("negative: 타임라인 조회가 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    timelineResult = {
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-ev-2"),
      refetch: refetchTimeline,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("타임라인 이벤트가 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 타임라인 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice 문구를 보여준다", async () => {
    timelineResult = {
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new ApiError(403, "raw forbidden detail", "trace-ev-3", "AUTHZ_FORBIDDEN"),
      refetch: refetchTimeline,
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden detail")).not.toBeInTheDocument();
  });

  it("positive: 타임라인이 실제로 비어 있으면(에러 없이 items=[]) 빈 상태를 보여준다", async () => {
    timelineResult = {
      data: { items: [], nextCursor: null, asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: refetchTimeline,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("타임라인 이벤트가 없습니다.")).toBeInTheDocument());
  });

  it("이벤트 항목을 aggregateType·action·outcome과 함께 렌더한다", () => {
    renderPage();

    expect(screen.getByText("MANDATE · ACTIVATE")).toBeInTheDocument();
    expect(screen.getByText(/seq 5/)).toBeInTheDocument();
  });

  it("다음 페이지가 없으면(nextCursor=null) '다음' 버튼이 비활성화된다", () => {
    renderPage();

    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  });

  it("다음 페이지가 있으면(nextCursor 존재) '다음' 버튼이 활성화된다", () => {
    timelineResult = {
      data: { items: [EVENT], nextCursor: "cursor-2", asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: refetchTimeline,
    };
    renderPage();

    expect(screen.getByRole("button", { name: "다음" })).not.toBeDisabled();
  });
});

describe("EvidenceChainPage 감사 체인 검증(chain:verify)", () => {
  it("테넌트 ID 없이 클릭하면 undefined로 mutate를 호출한다(system 체인)", () => {
    renderPage();

    clickVerify();

    expect(verifyMutate).toHaveBeenCalledWith(undefined);
  });

  it("테넌트 ID를 입력하면 그 값으로 mutate를 호출한다", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("테넌트 ID(선택)"), { target: { value: "tenant-9" } });
    clickVerify();

    expect(verifyMutate).toHaveBeenCalledWith("tenant-9");
  });

  it("검증 성공(verified=true)이면 SUCCESS 결과를 보여준다", () => {
    verifyResult = { ...verifyResult, isSuccess: true, data: { verified: true } };
    renderPage();

    // 타임라인 이벤트(EVENT.outcome="SUCCESS")도 같은 문구를 렌더하므로(중복 매치)
    // "검증 결과" 문단만 좁혀서 그 안에 SUCCESS가 있는지 본다.
    const resultParagraph = screen.getByText(/검증 결과/).closest("p");
    expect(resultParagraph).toHaveTextContent("SUCCESS");
  });

  it("negative: AUTHZ_FORBIDDEN(403) 검증 실패는 권한 없음 안내를 보여준다", () => {
    verifyResult = {
      ...verifyResult,
      isError: true,
      error: new ApiError(403, "raw forbidden verify detail", "trace-ev-4", "AUTHZ_FORBIDDEN"),
    };
    renderPage();

    expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw forbidden verify detail")).not.toBeInTheDocument();
  });
});
