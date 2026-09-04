import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AiosApiClient, ApiError } from "@aios/api-client";
import type { ExecutionCreateRequest } from "@aios/shared-types";
import { ExecutionControlPage } from "./ExecutionControlPage";

const mutateAsync = vi.fn();
const refetch = vi.fn();
let executionsData: unknown[] = [];
let executionsError: unknown = null;

vi.mock("@aios/shared-hooks", () => ({
  useExecutions: () => ({
    data: executionsData,
    isLoading: false,
    refetch,
    error: executionsError,
    isError: executionsError !== null,
  }),
  useCreateExecution: () => ({ mutateAsync, isPending: false, data: undefined }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
  refetch.mockReset();
  executionsData = [];
  executionsError = null;
  vi.unstubAllGlobals();
});

// task-1049 §3.7/§9 PLT-15: mutateAsync를 단순 spy로 두면 컴포넌트가 어떤 문자열을
// 넘겼는지만 확인하는 동어반복이 된다 — 아래 describe는 mutateAsync가 실제
// AiosApiClient(멱등 헤더 조립 포함)에 위임하도록 해 fetch로 나간 진짜
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

function stubFetchSequence(...responses: Array<{ body: unknown; status?: number }>): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn();
  for (const { body, status = 200 } of responses) {
    fetchMock.mockResolvedValueOnce(jsonResponse(status, body));
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function idempotencyKeyOf(fetchMock: ReturnType<typeof vi.fn>, call = 0): string | null {
  const [, init] = fetchMock.mock.calls[call] as [string, RequestInit];
  return new Headers(init.headers).get("Idempotency-Key");
}

function delegateToRealClient() {
  mutateAsync.mockImplementation(
    (vars: { body: ExecutionCreateRequest; idempotencyKey?: string }) =>
      realClient.createExecution(vars.body, vars.idempotencyKey),
  );
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ExecutionControlPage />
    </MemoryRouter>,
  );
}

function submitCreateForm(container: HTMLElement) {
  const strategyIdInput = container.querySelector("input") as HTMLInputElement;
  fireEvent.change(strategyIdInput, { target: { value: "strat-1" } });
  fireEvent.click(screen.getByRole("button", { name: "실행 생성" }));
}

// task-901 §3.3: 실행 생성 실패는 err.message를 직접 노출하지 않고 routeApiError로
// 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만 보여준다.
describe("ExecutionControlPage 실행 생성 에러 표시", () => {
  it("negative: POLICY_*(403) 거부는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
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
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() => expect(screen.getByText("실행 생성에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  it("VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400)는 BadRequestNotice 경로로 새로고침 안내를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "raw detail", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"),
    );
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() =>
      expect(
        screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });

  // task-1215 회귀: PLT-18/19 이후 ExecutionCreateError는 VALIDATION_INVALID_FIELD(400)
  // + details.fields=[]로 온다(task-1207 발견 1) — 필드별 인라인이 뜰 자리가 없어
  // 예전엔 실행 생성 실패가 화면에 아무 안내 없이 사라졌다. task-1214가 BadRequestNotice에
  // 배너 폴백을 고쳤으므로, 이 화면의 실행 생성 실패 경로가 실제로 거기까지 이어지는지
  // 잠근다(프로덕션 소스 무수정, 테스트만 — task-1215 decision).
  it("negative: VALIDATION_INVALID_FIELD(400) + 빈 details.fields는 서버 message 배너로 폴백한다(P0 무응답 회귀 방지)", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(
        400,
        "배분 자본이 최소 한도 미만입니다.",
        undefined,
        "VALIDATION_INVALID_FIELD",
        undefined,
        { fields: [] },
      ),
    );
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() =>
      expect(screen.getByText("배분 자본이 최소 한도 미만입니다.")).toBeInTheDocument(),
    );
  });
});

// spec §3.3 RESOURCE_NOT_FOUND(404)는 재시도 배너가 아니라 NotFoundState로 렌더한다
// (task-1056/ListingDetailPage와 동일 패턴, task-1089 배치2).
describe("ExecutionControlPage 실행 목록 404", () => {
  it("negative: 실행 목록 조회가 RESOURCE_NOT_FOUND(404)면 NotFoundState를 보여준다", () => {
    executionsError = new ApiError(404, "not found", undefined, "RESOURCE_NOT_FOUND");
    renderPage();

    expect(screen.getByText("실행 목록을 찾을 수 없습니다")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("negative: 실행 목록 조회가 RESOURCE_NOT_FOUND가 아닌 에러면 재시도 배너를 보여준다", () => {
    executionsError = new ApiError(500, "internal error", undefined, "INTERNAL_ERROR");
    renderPage();

    expect(
      screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."),
    ).toBeInTheDocument();
  });
});

// task-937 §3.3: 5xx는 classifyServerError, 409 STATE_CONCURRENCY_CONFLICT는
// useConflictRetry로 처리한다.
describe("ExecutionControlPage 5xx·409 재시도 배선", () => {
  it("negative: EXCHANGE_UNAVAILABLE(503)은 재시도 안내와 함께 다시 시도 버튼을 보여준다", async () => {
    mutateAsync.mockRejectedValue(new ApiError(503, "raw", undefined, "EXCHANGE_UNAVAILABLE"));
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() =>
      expect(
        screen.getByText("거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeEnabled();
  });

  it("negative: STATE_CONCURRENCY_CONFLICT(409)는 목록을 refetch한 뒤 새 Idempotency-Key로 자동 재제출해 성공한다", async () => {
    mutateAsync
      .mockRejectedValueOnce(new ApiError(409, "충돌", undefined, "STATE_CONCURRENCY_CONFLICT"))
      .mockResolvedValueOnce({ executionId: 1 });
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/다른 요청과 충돌했습니다/)).not.toBeInTheDocument();

    const firstKey = mutateAsync.mock.calls[0][0].idempotencyKey;
    const secondKey = mutateAsync.mock.calls[1][0].idempotencyKey;
    expect(firstKey).toBeTruthy();
    expect(secondKey).toBeTruthy();
    expect(secondKey).not.toBe(firstKey);
  });
});

describe("ExecutionControlPage 실행 생성 Idempotency-Key(§3.7) 실제 헤더 검증", () => {
  it("실행 생성 시 실제 요청 헤더에 규격(16~128자, [A-Za-z0-9_-])을 만족하는 Idempotency-Key를 싣는다", async () => {
    const fetchMock = stubFetch({ id: 1, status: "PENDING" });
    delegateToRealClient();
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(idempotencyKeyOf(fetchMock)).toMatch(IDEMPOTENCY_KEY_RE);
  });

  it("negative: 409 STATE_CONCURRENCY_CONFLICT는 refetch 후 자동 재제출하며, 두 요청 모두 서로 다른 실제 Idempotency-Key를 싣는다", async () => {
    const fetchMock = stubFetchSequence(
      {
        body: {
          error_code: "STATE_CONCURRENCY_CONFLICT",
          message: "충돌",
          details: {},
          trace_id: "t-exec-409",
          retry_after_seconds: null,
        },
        status: 409,
      },
      { body: { id: 2, status: "PENDING" } },
    );
    delegateToRealClient();
    const { container } = renderPage();

    submitCreateForm(container);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/다른 요청과 충돌했습니다/)).not.toBeInTheDocument();

    const firstKey = idempotencyKeyOf(fetchMock, 0);
    const secondKey = idempotencyKeyOf(fetchMock, 1);
    expect(firstKey).toMatch(IDEMPOTENCY_KEY_RE);
    expect(secondKey).toMatch(IDEMPOTENCY_KEY_RE);
    expect(secondKey).not.toBe(firstKey);
  });

  it("negative: 429 RATE_LIMIT_EXCEEDED는 매핑 문구를 보여주고 서버 원문은 노출하지 않으며, 재제출은 새 Idempotency-Key로 나간다", async () => {
    const firstFetch = stubFetch(
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "raw rate limit detail",
        details: {},
        trace_id: "t-exec-429",
        retry_after_seconds: 1,
      },
      429,
    );
    delegateToRealClient();
    const { container } = renderPage();

    submitCreateForm(container);
    await waitFor(() =>
      expect(
        screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw rate limit detail")).not.toBeInTheDocument();
    const firstKey = idempotencyKeyOf(firstFetch);

    const secondFetch = stubFetch({ id: 3, status: "PENDING" });
    submitCreateForm(container);
    await waitFor(() => expect(secondFetch).toHaveBeenCalledTimes(1));
    expect(idempotencyKeyOf(secondFetch)).not.toBe(firstKey);
  });
});
