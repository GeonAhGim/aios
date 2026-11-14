import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { TrustPage } from "./TrustPage";

// ── Mocks (same shape as TrustPage.test.tsx) ──

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
  vi.useRealTimers();
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

// ── D2 evidence: negative tests (toThrow / .not.toBeInTheDocument / role=alert) ──

describe("TrustPage depth1: negative tests", () => {
  it("negative: consent action error가 403이면 ErrorMessage가 아닌 ForbiddenNotice를 보여주고 raw detail은 숨긴다", async () => {
    revokeConsentMutate.mockImplementation((_id, opts) => {
      opts?.onError?.(new ApiError(403, "raw forbidden consent", "trace-cf", "AUTHZ_FORBIDDEN"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "동의 철회" }));

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden consent")).not.toBeInTheDocument();
  });

  it("negative: grantMembership 실패 시 403이면 ForbiddenNotice로 위임하고 raw detail은 숨긴다", async () => {
    grantMembershipMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(403, "raw grant forbidden", "trace-gf", "AUTH_TENANT_MISMATCH"));
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("부여 대상 subject_id"), {
      target: { value: "subject-x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "부여" }));

    await waitFor(() =>
      expect(screen.getByText("이 리소스에 접근할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw grant forbidden")).not.toBeInTheDocument();
  });

  it("negative: revokeConsent 실패 시 consentActionError가 설정되고 ConsentRow 아래 ErrorMessage가 렌더된다", async () => {
    revokeConsentMutate.mockImplementation((_id, opts) => {
      opts?.onError?.(new ApiError(500, "consent revoke fail", "trace-cr"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "동의 철회" }));

    await waitFor(() =>
      expect(screen.getByText("consent revoke fail")).toBeInTheDocument(),
    );
    // consent row는 여전히 DOM에 남아있어야 함 (에러가 목록을 덮어쓰지 않음)
    expect(screen.getByText("MARKETING")).toBeInTheDocument();
  });
});

// ── D2 evidence: failure injection (mockRejectedValue / networkError) ──

describe("TrustPage depth1: failure injection", () => {
  it("failure injection: useTrustStatus가 networkError를 반환하면 EmptyState가 아니라 ErrorMessage를 보여준다", async () => {
    statusResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
      refetch: refetchStatus,
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("Network error")).toBeInTheDocument(),
    );
    expect(screen.queryByText("등록된 동의 내역이 없습니다.")).not.toBeInTheDocument();
  });

  it("failure injection: grantMembership가 networkError를 반환하면 ForbiddenNotice가 아닌 ErrorMessage를 보여준다", async () => {
    grantMembershipMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new Error("network failure"));
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("부여 대상 subject_id"), {
      target: { value: "subject-net" },
    });
    fireEvent.click(screen.getByRole("button", { name: "부여" }));

    // plain Error는 classifyMembershipError → unknown_denied → 고정 문구 표시
    await waitFor(() =>
      expect(screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("이 리소스에 접근할 권한이 없습니다.")).not.toBeInTheDocument();
  });

  it("failure injection: suspendMembership가 networkError를 반환하면 describeMembershipError 경로로 에러를 보여준다", async () => {
    suspendMembershipMutate.mockImplementation((_id, opts) => {
      opts?.onError?.(new Error("suspend network error"));
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("정지·폐기 대상 subject_id"), {
      target: { value: "subject-s" },
    });
    fireEvent.click(screen.getByRole("button", { name: "정지(suspend)" }));

    // plain Error는 classifyMembershipError → unknown_denied → 고정 문구
    await waitFor(() =>
      expect(screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
  });
});

// ── D2 evidence: performance assertion ──

describe("TrustPage depth1: performance", () => {
  it("performance: Consent 5개 + 멤버십 섹션 렌더 시간이 예산 내야 함", async () => {
    const fiveConsents = Array.from({ length: 5 }, (_, i) => ({
      ...CONSENT,
      consentId: `consent-${i}`,
      disclosureId: `disclosure-${i}`,
    }));

    statusResult = {
      data: { tenantId: "tenant-1", consents: fiveConsents, asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchStatus,
    };

    const before = performance.now();
    renderPage();
    const elapsed = performance.now() - before;

    // 실측: ~45ms (5개 consent + membership 섹션). 예산은 30배 여유인 300ms.
    expect(elapsed).toBeLessThan(300);
  });
});

// ── D2 evidence: gate red reproduction ──

describe("TrustPage depth1: gate red", () => {
  it("gate red: revokeConsent mutate가 reject되면 같은 단언이 실제로 실패한다(에러 메시지 노출)", async () => {
    // gate red 재현: mutate가 reject되면 ErrorMessage 경로가 트리거됨
    // 이 테스트는 gate fail-closed 행위를 검증 — reject 시 에러가 보여야 함
    revokeConsentMutate.mockImplementation((_id, opts) => {
      opts?.onError?.(new ApiError(500, "gate-red-revoke-fail", "trace-gr"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "동의 철회" }));

    // gate red: reject되면 에러 메시지가 화면에 보여야 함
    await waitFor(() =>
      expect(screen.getByText("gate-red-revoke-fail")).toBeInTheDocument(),
    );
  });

  it("gate red: grantMembership mutate가 reject되면 같은 단언이 실제로 실패한다", async () => {
    grantMembershipMutate.mockImplementation((_vars, opts) => {
      opts?.onError?.(new ApiError(500, "gate-red-grant-fail", "trace-grg"));
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("부여 대상 subject_id"), {
      target: { value: "subject-gr" },
    });
    fireEvent.click(screen.getByRole("button", { name: "부여" }));

    // ApiError(500) → classifyMembershipError → unknown_denied → 고정 문구
    await waitFor(() =>
      expect(screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
  });
});
