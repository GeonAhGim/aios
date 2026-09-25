import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { SafetyControlsPage } from "./SafetyControlsPage";

const deactivateMutate = vi.fn();
const evaluateRecoveryMutate = vi.fn();
const activateSafetyControlMutate = vi.fn();
const evaluateRiskGateMutate = vi.fn();
const approveRuleBundleMutate = vi.fn();
const activateRuleBundleMutate = vi.fn();
const refetchControls = vi.fn();

const CONTROL = {
  id: "c1",
  scope: "GLOBAL",
  scopeRef: "tenant-1",
  state: "ACTIVE",
  reason: "circuit breaker halted",
  fenceToken: 1,
  createdAt: "2026-09-09T00:00:00Z",
  deactivatedAt: null,
  idempotencyDigest: null,
  schemaVersion: "v1",
};

let controlsResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = {
  data: { controls: [CONTROL], asOf: "2026-09-09T00:00:00Z" },
  isLoading: false,
  isError: false,
  error: null,
  refetch: refetchControls,
};

vi.mock("@aios/shared-hooks", () => ({
  useSafetyControls: () => controlsResult,
  useDeactivateSafetyControl: () => ({ mutate: deactivateMutate, isPending: false }),
  useEvaluateRecovery: () => ({ mutate: evaluateRecoveryMutate, isPending: false }),
  useActivateSafetyControl: () => ({ mutate: activateSafetyControlMutate, isPending: false }),
  useEvaluateRiskGate: () => ({ mutate: evaluateRiskGateMutate, isPending: false }),
  useApproveRuleBundle: () => ({ mutate: approveRuleBundleMutate, isPending: false }),
  useActivateRuleBundle: () => ({ mutate: activateRuleBundleMutate, isPending: false }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  deactivateMutate.mockReset();
  evaluateRecoveryMutate.mockReset();
  activateSafetyControlMutate.mockReset();
  evaluateRiskGateMutate.mockReset();
  approveRuleBundleMutate.mockReset();
  activateRuleBundleMutate.mockReset();
  refetchControls.mockReset();
  controlsResult = {
    data: { controls: [CONTROL], asOf: "2026-09-09T00:00:00Z" },
    isLoading: false,
    isError: false,
    error: null,
    refetch: refetchControls,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <SafetyControlsPage />
    </MemoryRouter>,
  );
}

function clickDeactivate() {
  fireEvent.click(screen.getByRole("button", { name: "즉시 해제" }));
}

function clickEvaluateRecovery() {
  fireEvent.click(screen.getByRole("button", { name: "복구 평가(R-53)" }));
}

describe("SafetyControlsPage 목록 조회 실패/빈 상태 표시", () => {
  it("negative: 목록 조회가 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    controlsResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-sc-1"),
      refetch: refetchControls,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("활성 안전 통제가 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 목록 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice 문구를 보여준다", async () => {
    controlsResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw forbidden list detail", "trace-sc-2", "AUTHZ_FORBIDDEN"),
      refetch: refetchControls,
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden list detail")).not.toBeInTheDocument();
  });

  it("positive: 활성 통제가 실제로 비어 있으면(에러 없이 controls=[]) 빈 상태를 보여준다", async () => {
    controlsResult = {
      data: { controls: [], asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchControls,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("활성 안전 통제가 없습니다.")).toBeInTheDocument());
  });

  it("ACTIVE가 아닌 통제(INACTIVE)는 해제/복구평가 액션 버튼을 보여주지 않는다", () => {
    controlsResult = {
      data: { controls: [{ ...CONTROL, state: "INACTIVE", deactivatedAt: "2026-09-09T01:00:00Z" }], asOf: "2026-09-09T01:00:00Z" },
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchControls,
    };
    renderPage();

    expect(screen.queryByRole("button", { name: "즉시 해제" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "복구 평가(R-53)" })).not.toBeInTheDocument();
  });
});

describe("SafetyControlsPage 해제(deactivate) 실패 표시", () => {
  it("negative: AUTHZ_FORBIDDEN(403) 해제 실패는 권한 없음 안내를 보여준다", async () => {
    deactivateMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(403, "raw forbidden detail", "trace-1", "AUTHZ_FORBIDDEN"));
    });
    renderPage();

    clickDeactivate();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden detail")).not.toBeInTheDocument();
  });

  it("deactivate.mutate는 controlId를 그대로 넘긴다", () => {
    renderPage();

    clickDeactivate();

    expect(deactivateMutate).toHaveBeenCalledWith("c1", expect.anything());
  });
});

describe("SafetyControlsPage 복구 평가(evaluate-recovery)", () => {
  it("승인 요청 ID 없이 클릭하면 서버 왕복 없이 입력 오류를 보여준다", () => {
    renderPage();

    clickEvaluateRecovery();

    expect(screen.getByText("승인 요청 ID를 숫자로 입력하세요.")).toBeInTheDocument();
    expect(evaluateRecoveryMutate).not.toHaveBeenCalled();
  });

  it("승인 요청 ID를 입력하면 controlId·approvalId(number)·evidenceRef로 mutate를 호출한다", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("승인 요청 ID"), { target: { value: "42" } });
    fireEvent.change(screen.getByPlaceholderText("증빙 참조(evidence_ref)"), { target: { value: "ev-1" } });
    clickEvaluateRecovery();

    expect(evaluateRecoveryMutate).toHaveBeenCalledWith(
      { controlId: "c1", body: { approvalId: 42, evidenceRef: "ev-1" } },
      expect.anything(),
    );
  });

  it("negative: POLICY_*(403) 복구 평가 실패는 ForbiddenNotice 사유 목록을 보여준다", async () => {
    evaluateRecoveryMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(
        new ApiError(403, "복구가 거부되었습니다.", "trace-2", "POLICY_LIVE_BLOCKED", undefined, {
          reason_codes: ["POLICY_LIVE_BLOCKED"],
        }),
      );
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("승인 요청 ID"), { target: { value: "42" } });
    clickEvaluateRecovery();

    // DenialReasons가 배너 <p>와 같은 문구를 사유 목록 <li>에도 렌더한다
    // (DisputeManagementPage.test.tsx와 동일 이유) — getAllByText로 둘 다 있는지만 확인한다.
    await waitFor(() =>
      expect(screen.getAllByText("실거래 모드에서는 허용되지 않는 작업입니다.").length).toBeGreaterThan(0),
    );
  });

  it("성공하면 결과 배너에 outcome과 사유를 보여준다", async () => {
    evaluateRecoveryMutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({
        id: "d1",
        gateKind: "RECOVERY",
        outcome: "ALLOW",
        reasonCodes: [],
        evaluatedAt: "2026-09-09T00:00:00Z",
        expiresAt: "2026-09-09T01:00:00Z",
        traceId: "trace-3",
      });
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("승인 요청 ID"), { target: { value: "42" } });
    clickEvaluateRecovery();

    await waitFor(() => expect(screen.getByText(/복구 평가 결과/)).toBeInTheDocument());
    expect(screen.getByText("ALLOW")).toBeInTheDocument();
  });
});

describe("SafetyControlsPage 개통(admin activate)", () => {
  it("scope·scopeRef·reason으로 activate.mutate를 호출한다", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("대상 참조(scope_ref, 선택)"), {
      target: { value: "tenant-9" },
    });
    fireEvent.change(screen.getByPlaceholderText("사유"), { target: { value: "긴급 정지" } });
    fireEvent.click(screen.getByRole("button", { name: "개통" }));

    expect(activateSafetyControlMutate).toHaveBeenCalledWith(
      { scope: "GLOBAL", scopeRef: "tenant-9", reason: "긴급 정지" },
      expect.anything(),
    );
  });

  it("negative: AUTHZ_FORBIDDEN(403) 개통 실패는 권한 없음 안내를 보여준다", async () => {
    activateSafetyControlMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(403, "raw forbidden activate", "trace-4", "AUTHZ_FORBIDDEN"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "개통" }));

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden activate")).not.toBeInTheDocument();
  });
});

describe("SafetyControlsPage 리스크 게이트 평가(evaluate)", () => {
  it("gateKind·connectionId로 evaluate.mutate를 호출한다", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("커넥션 ID(선택)"), { target: { value: "conn-1" } });
    fireEvent.click(screen.getByRole("button", { name: "평가" }));

    expect(evaluateRiskGateMutate).toHaveBeenCalledWith(
      { gateKind: "PRE_TRADE", connectionId: "conn-1" },
      expect.anything(),
    );
  });

  it("성공하면 결과 배너에 outcome을 보여준다", async () => {
    evaluateRiskGateMutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({
        id: "e1",
        gateKind: "PRE_TRADE",
        outcome: "DENY",
        reasonCodes: [],
        obligations: [],
        ruleVersion: "v1",
        evaluatedAt: "2026-09-09T00:00:00Z",
        expiresAt: null,
        traceId: "trace-5",
        schemaVersion: "v1",
      });
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "평가" }));

    await waitFor(() => expect(screen.getByText(/평가 결과/)).toBeInTheDocument());
    expect(screen.getByText("DENY")).toBeInTheDocument();
  });

  it("negative: evaluate 실패는 ErrorMessage를 보여준다", async () => {
    evaluateRiskGateMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(500, "평가에 실패했습니다.", "trace-6"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "평가" }));

    await waitFor(() => expect(screen.getByText("평가에 실패했습니다.")).toBeInTheDocument());
  });
});

describe("SafetyControlsPage 룰번들 승인/활성화", () => {
  it("승인 버튼은 bundleId·approvalRef로 approve.mutate를 호출한다", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("룰번들 ID"), { target: { value: "bundle-1" } });
    fireEvent.change(screen.getByPlaceholderText("승인 참조(approval_ref)"), {
      target: { value: "appr-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    expect(approveRuleBundleMutate).toHaveBeenCalledWith(
      { bundleId: "bundle-1", body: { approvalRef: "appr-1" } },
      expect.anything(),
    );
  });

  it("활성화 버튼은 bundleId로 activate.mutate를 호출한다", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("룰번들 ID"), { target: { value: "bundle-2" } });
    fireEvent.click(screen.getByRole("button", { name: "활성화" }));

    expect(activateRuleBundleMutate).toHaveBeenCalledWith("bundle-2", expect.anything());
  });

  it("negative: AUTHZ_FORBIDDEN(403) 승인 실패는 권한 없음 안내를 보여준다", async () => {
    approveRuleBundleMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(403, "raw forbidden bundle", "trace-7", "AUTHZ_FORBIDDEN"));
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("룰번들 ID"), { target: { value: "bundle-3" } });
    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden bundle")).not.toBeInTheDocument();
  });

  it("성공하면 룰번들 상태 배너를 보여준다", async () => {
    activateRuleBundleMutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({
        id: "bundle-4",
        scope: "GLOBAL",
        version: "1",
        ruleHash: "hash",
        engineVersion: "1",
        policySnapshot: {},
        state: "ACTIVE",
        effectiveFrom: null,
        effectiveTo: null,
        createdBy: "admin",
        approvedBy: "admin",
        approvalRef: "appr-1",
        approvedAt: null,
        activatedAt: "2026-09-09T00:00:00Z",
        retiredAt: null,
      });
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("룰번들 ID"), { target: { value: "bundle-4" } });
    fireEvent.click(screen.getByRole("button", { name: "활성화" }));

    await waitFor(() => expect(screen.getByText(/룰번들 상태/)).toBeInTheDocument());
  });
});
