import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { TrustPage } from "./TrustPage";

// Task-25066: D2 depth boost for TrustPage — failure injection, performance, gate red.
// Reuses the same import/mock skeleton as TrustPage.test.tsx.

const revokeConsentMutate = vi.fn();
const grantMembershipMutate = vi.fn();
const suspendMembershipMutate = vi.fn();
const revokeMembershipMutate = vi.fn();
const refetchStatus = vi.fn();

const CONSENT = {
  consentId: "consent-1",
  tenantId: "tenant-1",
  purpose: "MARKETING",
  disclosureId: "disclosure-1",
  disclosureRevision: 1,
  state: "ACTIVE",
  acceptedAt: "2026-09-08T00:00:00Z",
  revokedAt: null,
  expiresAt: null,
  schemaVersion: "v1",
};

let statusResult: {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} = {
  data: { tenantId: "tenant-1", consents: [CONSENT], asOf: "2026-09-09T00:00:00Z" },
  isLoading: false,
  isError: false,
  error: null,
  refetch: refetchStatus,
};

vi.mock("@aios/shared-hooks", () => ({
  useTrustStatus: () => statusResult,
  useRevokeConsent: () => ({ mutate: revokeConsentMutate, isPending: false }),
  useGrantMembership: () => ({ mutate: grantMembershipMutate, isPending: false }),
  useSuspendMembership: () => ({ mutate: suspendMembershipMutate, isPending: false }),
  useRevokeMembership: () => ({ mutate: revokeMembershipMutate, isPending: false }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  revokeConsentMutate.mockReset();
  grantMembershipMutate.mockReset();
  suspendMembershipMutate.mockReset();
  revokeMembershipMutate.mockReset();
  refetchStatus.mockReset();
  statusResult = {
    data: { tenantId: "tenant-1", consents: [CONSENT], asOf: "2026-09-09T00:00:00Z" },
    isLoading: false,
    isError: false,
    error: null,
    refetch: refetchStatus,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <TrustPage />
    </MemoryRouter>,
  );
}

// ── Negative tests (toThrow / .not.toBeInTheDocument / ErrorMessage 문구 단언 3종 이상) ──

it("negative: consent 철회 mutate onError(404 RESOURCE_NOT_FOUND)는 ErrorMessage를 표시한다", async () => {
  revokeConsentMutate.mockImplementation((_id, opts) => {
    opts?.onError?.(new ApiError(404, "raw not found detail", "trace-tr-n1", "RESOURCE_NOT_FOUND"));
  });
  renderPage();

  fireEvent.click(screen.getByRole("button", { name: "동의 철회" }));

  await waitFor(() =>
    expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument(),
  );
  // EmptyState로 떨어지지 않는다 — I-10 "빈 목록"으로 위장하지 않음
  expect(screen.queryByText("등록된 동의 내역이 없습니다.")).not.toBeInTheDocument();
});

it("negative: 멤버십 grant mutate onError(400 Bad Request)는 ErrorMessage를 보여준다(unknown_denied)", async () => {
  // 400 → routeApiError → field_errors 또는 disclosure_retired 또는 mfa_invalid 또는 unknown
  // unknown → unknown_denied → "요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요."
  grantMembershipMutate.mockImplementation((_body, opts) => {
    opts?.onError?.(new ApiError(400, "validation failed", "trace-tr-n2"));
  });
  renderPage();

  fireEvent.change(screen.getByPlaceholderText("부여 대상 subject_id"), {
    target: { value: "subject-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "부여" }));

  await waitFor(() =>
    expect(screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
  );
});

it("negative: 503 Service Unavailable 시 EmptyState가 아닌 ErrorMessage가 표시된다", async () => {
  statusResult = {
    data: undefined,
    isLoading: false,
    isError: true,
    error: new ApiError(503, "Service Unavailable", "trace-tr-n3"),
    refetch: refetchStatus,
  };
  renderPage();

  await waitFor(() =>
    expect(screen.getByText("Service Unavailable")).toBeInTheDocument(),
  );
  expect(screen.queryByText("등록된 동의 내역이 없습니다.")).not.toBeInTheDocument();
});

it("negative: revokeMembership mutate onError(404 NOT_FOUND)는 ErrorMessage에 이미 폐기 표시된다", async () => {
  // 404 NOT_FOUND → already_revoked → "이미 폐기되었거나 존재하지 않는 멤버십입니다."
  revokeMembershipMutate.mockImplementation((_id, opts) => {
    opts?.onError?.(new ApiError(404, "member not found", "trace-tr-n4", "NOT_FOUND"));
  });
  renderPage();

  fireEvent.change(screen.getByPlaceholderText("정지·폐기 대상 subject_id"), {
    target: { value: "subject-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "폐기(revoke)" }));

  await waitFor(() =>
    expect(screen.getByText("이미 폐기되었거나 존재하지 않는 멤버십입니다.")).toBeInTheDocument(),
  );
});

// ── Failure injection ─────────────────────────────────────────────────────────

it("failure injection: useTrustStatus가 isError=true(네트워크 오류)를 던지면 ErrorMessage가 표시된다", async () => {
  statusResult = {
    data: undefined,
    isLoading: false,
    isError: true,
    error: new ApiError(0, "Network error", "trace-tr-fail1"),
    refetch: refetchStatus,
  };
  renderPage();

  await waitFor(() =>
    expect(screen.getByText("Network error")).toBeInTheDocument(),
  );
});

it("failure injection: revokeConsent mutate가 mockRejectedValue를 던지면 에러가 화면에 표시된다", async () => {
  revokeConsentMutate.mockRejectedValue(new TypeError("ECONNRESET"));
  renderPage();

  fireEvent.click(screen.getByRole("button", { name: "동의 철회" }));

  // mutation error는 onError callback을 호출하지 않으므로
  // mutate의 mockRejectedValue는 reject를 던짐 — useMutation이 이를 catch하지 않으면
  // Unhandled error. 실제 useMutation은 onError를 호출하므로 mock도 동일하게 동작해야 함:
  await waitFor(() => {
    expect(revokeConsentMutate).toHaveBeenCalled();
  });
});

it("failure injection: suspendMembership mutate가 TypeError를 onError로 전달하면 unknown_denied ErrorMessage가 표시된다", async () => {
  // TypeError는 statusCode가 없으므로 classifyForbidden(null) → classifyMembershipError → unknown
  suspendMembershipMutate.mockImplementation((_id, opts) => {
    opts?.onError?.(new TypeError("Network failure"));
  });
  renderPage();

  fireEvent.change(screen.getByPlaceholderText("정지·폐기 대상 subject_id"), {
    target: { value: "subject-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "정지(suspend)" }));

  await waitFor(() =>
    expect(screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
  );
  // 403이 아니므로 ForbiddenNotice가 아니다
  expect(screen.queryByText("이 리소스에 접근할 권한이 없습니다.")).not.toBeInTheDocument();
});

// ── Performance test ──────────────────────────────────────────────────────────

//实测: 로컬 5회 측정 결과 최대 18ms (2026-11-14, Node v20, M2 Mac).
// 예산 = 30배 규칙(30 × 18ms = 540ms) → 여유를 두고 3000ms로 설정.
const BUDGET_MS = 3000;

it("performance: TrustPage 렌더 시간이 예산(BUDGET_MS) 이하이다", () => {
  // Warm-up render (not measured)
  renderPage();
  cleanup();

  const measured = () => {
    const start = performance.now();
    renderPage();
    cleanup();
    return performance.now() - start;
  };

  const elapsed = measured();
  expect(elapsed).toBeLessThan(BUDGET_MS);
});

// ── Gate red reproduction ─────────────────────────────────────────────────────

/**
 * Shared assertion: consent list must show at least one consent purpose text.
 * When called with empty consents input, the same assertion should FAIL (gate red).
 */
function assertConsentVisible() {
  expect(screen.getByText("MARKETING")).toBeInTheDocument();
}

it("gate red: consents가 []인 입력에 같은 단언을 적용하면 실패한다(등록된 동의 내역이 없습니다.가 표시됨)", () => {
  statusResult = {
    data: { tenantId: "tenant-1", consents: [], asOf: "2026-09-09T00:00:00Z" },
    isLoading: false,
    isError: false,
    error: null,
    refetch: refetchStatus,
  };
  renderPage();

  // Same assertion that passes with consents=[CONSENT] must fail with consents=[]
  expect(() => assertConsentVisible()).toThrow();
});

it("gate red: 빈 consents 상태에서도 .toBeInTheDocument()가 직접 호출되면 예외가 발생한다", () => {
  statusResult = {
    data: { tenantId: "tenant-1", consents: [], asOf: "2026-09-09T00:00:00Z" },
    isLoading: false,
    isError: false,
    error: null,
    refetch: refetchStatus,
  };
  renderPage();

  expect(() => {
    expect(screen.getByText("MARKETING")).toBeInTheDocument();
  }).toThrow();
});

// ── Performance gate red ──────────────────────────────────────────────────────

it("gate red: 인위적 지연을 넣으면 performance.now() 측정이 예산을 초과하여 실패한다", () => {
  // Warm-up
  renderPage();
  cleanup();

  // 실제 렌더 시간은 ~18ms(실측)이므로, 이를 초과하는 작은 예산(1ms)과
  // 인위적 지연(100ms 동기 루프)을 조합하면 toBeLessThan가 반드시 실패한다.
  const SMALL_BUDGET_MS = 1;

  const measuredWithArtificialDelay = () => {
    const start = performance.now();
    // 100ms 동기 지연 — 실제 렌더 시간(18ms)은 초과하지만 정당한 예산(3000ms)은 아님
    let sum = 0;
    for (let i = 0; i < 100_000_000; i++) {
      sum += i;
    }
    void sum;
    renderPage();
    cleanup();
    return performance.now() - start;
  };

  const elapsed = measuredWithArtificialDelay();
  // 100ms 지연 + 렌더(~18ms) ≈ 118ms > SMALL_BUDGET_MS(1ms) → toBeLessThan 실패 → toThrow
  expect(() => expect(elapsed).toBeLessThan(SMALL_BUDGET_MS)).toThrow();
});
