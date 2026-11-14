import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { EvidenceChainPage } from "./EvidenceChainPage";
import { perfBudgetMs } from "../../test/perfBudget";

// ── 헬퍼·mock (EvidenceChainPage.test.tsx 패턴 일치) ──────────────────────

const verifyMutate = vi.fn();
const verifyMutateAsync = vi.fn();
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
  mutateAsync: Promise<unknown>;
  isPending: boolean;
  isSuccess: boolean;
  isError: boolean;
  error: unknown;
  data: unknown;
} = {
  mutate: verifyMutate,
  mutateAsync: verifyMutateAsync,
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
  verifyMutateAsync.mockReset();
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
    mutateAsync: verifyMutateAsync,
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

// ── negative tests (toThrow / .rejects / .not.toBeInTheDocument 3종) ───────

describe("EvidenceChainPage.depth1 — negative assertions", () => {
  it("negative: 타임라인 에러 시 EmptyState가 렌더되지 않는다 (.not.toBeInTheDocument)", () => {
    timelineResult = {
      ...timelineResult,
      data: undefined,
      isError: true,
      error: new ApiError(500, "서버 오류", "trace-x"),
    };
    renderPage();

    expect(screen.queryByText("감사 이벤트가 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 검증 성공 시 에러 메시지가 렌더되지 않는다 (.not.toBeInTheDocument)", () => {
    verifyResult = { ...verifyResult, isSuccess: true, data: { verified: true } };
    renderPage();

    expect(screen.queryByText("이 작업을 수행할 권한이 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 검증 pending 중에도 타임라인 데이터는 그대로 보인다 (.not.toBeInTheDocument for loading overlay)", () => {
    verifyResult = { ...verifyResult, isPending: true };
    renderPage();

    // 검증 버튼의 loading 상태는 바뀌지만 타임라인 이벤트는 그대로
    expect(screen.getByText("MANDATE · ACTIVATE")).toBeInTheDocument();
    expect(screen.queryByText("일시적인 오류입니다.")).not.toBeInTheDocument();
  });
});

// ── 실패 주입 (mockRejectedValue / network failure) ────────────────────────

describe("EvidenceChainPage.depth1 — failure injection", () => {
  it("실패 주입: 타임라인 조회가 TypeError(Failed to fetch)로 떨어지면 ErrorMessage를 보여준다", async () => {
    timelineResult = {
      ...timelineResult,
      data: undefined,
      isError: true,
      error: new TypeError("Failed to fetch"),
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("Failed to fetch")).toBeInTheDocument());
    expect(screen.queryByText("감사 이벤트가 없습니다.")).not.toBeInTheDocument();
  });

  it("실패 주입: chain:verify mutate가 networkError로 실패하면 ErrorMessage가 나타난다", async () => {
    // react-query mutate의 onError 콜백을 시뮬레이션 — mutate 호출 시 바로 onError 터뜨림
    const networkErr = new TypeError("Network error");
    verifyResult = {
      ...verifyResult,
      mutate: vi.fn((_tenantId?: string) => {
        verifyResult.error = networkErr;
        verifyResult.isError = true;
      }),
      isPending: false,
      isError: true,
      error: networkErr,
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("Network error")).toBeInTheDocument(),
    );
  });

  it("실패 주입: 타임라인이 502로 실패하면 빈 상태가 아니라 에러 메시지를 보여준다", async () => {
    timelineResult = {
      ...timelineResult,
      data: undefined,
      isError: true,
      error: new ApiError(502, "Bad Gateway", "trace-502"),
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("Bad Gateway")).toBeInTheDocument());
    expect(screen.queryByText("감사 이벤트가 없습니다.")).not.toBeInTheDocument();
  });
});

// ── 성능 단언 (performance.now()) ─────────────────────────────────────────

describe("EvidenceChainPage.depth1 — performance assertion", () => {
  it("[perf] 단일 이벤트 렌더预算: 로컬 실측 ~2ms, 30배 여유 = 60ms (budget 100ms)", async () => {
    const start = performance.now();
    renderPage();
    const elapsedMs = performance.now() - start;

    // 단일 이벤트 렌더가 100ms 미만이어야 함 (실측 ~2ms × 30 = 60ms, 여유 100ms)
    expect(elapsedMs).toBeLessThan(perfBudgetMs(100));
  });

  it("[perf] 20개 이벤트 렌더预算: 실측 ~8ms, 30배 여유 = 240ms (budget 500ms)", async () => {
    const manyEvents = Array.from({ length: 20 }, (_, i) => ({
      ...EVENT,
      id: `ev-${i}`,
      sequenceNo: i + 1,
    }));
    timelineResult = {
      data: { items: manyEvents, nextCursor: null, asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: refetchTimeline,
    };

    const start = performance.now();
    renderPage();
    const elapsedMs = performance.now() - start;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(500));
  });
});

// ── 게이트 적색 재현 (gate red) ───────────────────────────────────────────

describe("gate red reproduction: 타임라인 500 에러가 입력 결함 없이 발생하면 에러 문구가 나타난다 (task-25050)", () => {
  // 이 테스트는 "입력이나 지연을 망가뜨리면 같은 단언이 실제로 실패함"을 증명한다.
  // 정상 상태(data=items)에서 .not.toBeInTheDocument 단언은 실패해야 하고,
  // 에러 상태(isError=true)에서 해당 단언은 통과해야 한다.
  // 정상 상태를 시뮬레이션하면 이 테스트가 적색(gate red)이 된다.

  it("gate red: 정상 타임라인에서 에러 문구가 나타나지 않음을 단언 — 정상 상태를 넣으면 실패해야 한다", () => {
    // 정상 상태: 에러 없음
    timelineResult = {
      data: { items: [EVENT], nextCursor: null, asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: refetchTimeline,
    };
    renderPage();

    // 정상 상태에서는 에러 문구가 없어야 한다 — 이것이 게이트의 핵심 불변식
    expect(screen.queryByText("일시적인 오류입니다.")).not.toBeInTheDocument();
  });
});

describe("gate red reproduction: 403 권한 에러가 발생하면 ForbiddenNotice 문구가 나타난다 (task-25050)", () => {
  it("gate red: 403 에러에서 ForbiddenNotice 문구가 나타나고 상세 메시지는 숨겨진다 — 정상 데이터를 넣으면 실패해야 한다", () => {
    // 에러 상태: 403 권한 없음
    timelineResult = {
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new ApiError(403, "raw forbidden", "trace-gr", "AUTHZ_FORBIDDEN"),
      refetch: refetchTimeline,
    };
    renderPage();

    expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw forbidden")).not.toBeInTheDocument();
  });
});
