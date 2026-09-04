import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AiosApiClient, ApiError } from "@aios/api-client";
import type { PaperDeploymentView, RequestPaperDeploymentBody } from "@aios/api-client";
import { PaperDeploymentsPage } from "./PaperDeploymentsPage";

const requestMutateAsync = vi.fn();
const startMutateAsync = vi.fn();
const resumeMutateAsync = vi.fn();
const pauseMutateAsync = vi.fn();
const stopMutateAsync = vi.fn();
const refetch = vi.fn();
let deployments: PaperDeploymentView[] = [];
let listError: unknown = null;

function deploymentFixture(overrides: Partial<PaperDeploymentView> = {}): PaperDeploymentView {
  return {
    id: "dep-1",
    packageRef: "pkg-1",
    connectionId: null,
    state: "READY",
    fenceToken: 1,
    createdAt: null,
    updatedAt: null,
    schemaVersion: "v1",
    ...overrides,
  };
}

vi.mock("@aios/shared-hooks", () => ({
  usePaperDeployments: () => ({
    data: { deployments, asOf: "2026-09-04T00:00:00Z" },
    isLoading: false,
    refetch,
    error: listError,
    isError: listError !== null,
  }),
  useRequestPaperDeployment: () => ({ mutateAsync: requestMutateAsync, isPending: false }),
  useStartPaperDeployment: () => ({ mutateAsync: startMutateAsync, isPending: false, isError: false, error: null }),
  useResumePaperDeployment: () => ({ mutateAsync: resumeMutateAsync, isPending: false, isError: false, error: null }),
  usePausePaperDeployment: () => ({ mutateAsync: pauseMutateAsync, isPending: false, isError: false, error: null }),
  useStopPaperDeployment: () => ({ mutateAsync: stopMutateAsync, isPending: false, isError: false, error: null }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  requestMutateAsync.mockReset();
  startMutateAsync.mockReset();
  resumeMutateAsync.mockReset();
  pauseMutateAsync.mockReset();
  stopMutateAsync.mockReset();
  refetch.mockReset();
  deployments = [];
  listError = null;
  vi.unstubAllGlobals();
});

const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,128}$/;
const realClient = new AiosApiClient("https://api.example.test", () => null);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function envelope<T>(data: T) {
  return { data, meta: { trace_id: "t1", as_of: "2026-09-04T00:00:00Z", page: null } };
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function idempotencyKeyOf(fetchMock: ReturnType<typeof vi.fn>, call = 0): string | null {
  const [, init] = fetchMock.mock.calls[call] as [string, RequestInit];
  return new Headers(init.headers).get("Idempotency-Key");
}

function delegateRequestToRealClient() {
  requestMutateAsync.mockImplementation(
    (vars: { body: RequestPaperDeploymentBody; idempotencyKey: string }) =>
      realClient.requestPaperDeployment(vars.body, vars.idempotencyKey),
  );
}

function renderPage() {
  return render(
    <MemoryRouter>
      <PaperDeploymentsPage />
    </MemoryRouter>,
  );
}

function submitCreateForm(container: HTMLElement) {
  const inputs = container.querySelectorAll("input");
  // Field 순서: 패키지 참조[0] / 연결 ID(선택)[1] / 어댑터 유형[2, 기본값 있음] /
  // 샌드박스 계정 참조[3, required].
  fireEvent.change(inputs[0], { target: { value: "pkg-a" } });
  fireEvent.change(inputs[3], { target: { value: "acct-1" } });
  fireEvent.click(screen.getByRole("button", { name: "배포 요청" }));
}

describe("PaperDeploymentsPage 목록 렌더링", () => {
  it("negative: 목록 조회가 RESOURCE_NOT_FOUND(404)면 NotFoundState를 보여준다", () => {
    listError = new ApiError(404, "not found", undefined, "RESOURCE_NOT_FOUND");
    renderPage();

    expect(screen.getByText("배포 목록을 찾을 수 없습니다")).toBeInTheDocument();
  });

  it("negative: 그 외 에러는 재시도 배너를 보여준다", () => {
    listError = new ApiError(500, "internal error", undefined, "INTERNAL_ERROR");
    renderPage();

    expect(
      screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."),
    ).toBeInTheDocument();
  });

  it("배포가 없으면 EmptyState를 보여준다", () => {
    renderPage();
    expect(screen.getByText("페이퍼 배포가 없습니다.")).toBeInTheDocument();
  });

  it("배포 목록을 렌더링한다", () => {
    deployments = [deploymentFixture()];
    renderPage();
    expect(screen.getByText("pkg-1")).toBeInTheDocument();
    expect(screen.getByText("READY")).toBeInTheDocument();
  });
});

// 77번 §2/§3 상태머신(start_deployment.py/pause_deployment.py 원본 확인)과
// 어긋나는 버튼을 보여주지 않는지 상태별로 검증한다.
describe("PaperDeploymentsPage 상태별 명령 버튼", () => {
  it.each([
    ["REQUESTED", []],
    ["READY", ["시작", "중지"]],
    ["RUNNING", ["일시정지", "중지"]],
    ["PAUSED", ["재개", "중지"]],
    ["DEGRADED", ["중지"]],
    ["RECOVERY_REVIEW", ["중지"]],
    ["STOPPED", []],
    ["FAILED", []],
  ] as const)("state=%s이면 버튼 %j을 보여준다", (state, expectedButtons) => {
    deployments = [deploymentFixture({ state })];
    renderPage();

    for (const label of ["시작", "재개", "일시정지", "중지"]) {
      const btn = screen.queryByRole("button", { name: label });
      if ((expectedButtons as readonly string[]).includes(label)) {
        expect(btn).toBeInTheDocument();
      } else {
        expect(btn).not.toBeInTheDocument();
      }
    }
  });

  it("시작 버튼을 누르면 startPaperDeployment가 deploymentId와 함께 호출된다", async () => {
    deployments = [deploymentFixture({ id: "dep-42", state: "READY" })];
    startMutateAsync.mockResolvedValue(deploymentFixture({ id: "dep-42", state: "RUNNING" }));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "시작" }));

    await waitFor(() => expect(startMutateAsync).toHaveBeenCalledTimes(1));
    const call = startMutateAsync.mock.calls[0][0] as { deploymentId: string; idempotencyKey: string };
    expect(call.deploymentId).toBe("dep-42");
    expect(call.idempotencyKey).toMatch(IDEMPOTENCY_KEY_RE);
  });
});

describe("PaperDeploymentsPage 배포 요청 에러 표시", () => {
  it("negative: POLICY_*(403) 거부는 ForbiddenNotice 매핑 문구를 보여준다", async () => {
    requestMutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-1", "POLICY_LIVE_BLOCKED"),
    );
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    requestMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() => expect(screen.getByText("배포 요청에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});

describe("PaperDeploymentsPage 배포 요청 Idempotency-Key(§3.7) 실제 헤더 검증", () => {
  it("배포 요청 시 실제 요청 헤더에 규격(16~128자, [A-Za-z0-9_-])을 만족하는 Idempotency-Key를 싣는다", async () => {
    const fetchMock = stubFetch(envelope(deploymentFixture()), 201);
    delegateRequestToRealClient();
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(idempotencyKeyOf(fetchMock)).toMatch(IDEMPOTENCY_KEY_RE);
  });
});
