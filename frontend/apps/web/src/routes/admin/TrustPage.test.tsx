import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { TrustPage } from "./TrustPage";

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

describe("TrustPage 동의(Consent) 현황 조회 실패/빈 상태 표시", () => {
  it("negative: 상태 조회가 500으로 실패하면 빈 상태가 아니라 ErrorMessage를 보여준다", async () => {
    statusResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-tr-1"),
      refetch: refetchStatus,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
    expect(screen.queryByText("등록된 동의 내역이 없습니다.")).not.toBeInTheDocument();
  });

  it("negative: 상태 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice 문구를 보여준다", async () => {
    statusResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw forbidden detail", "trace-tr-2", "AUTHZ_FORBIDDEN"),
      refetch: refetchStatus,
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw forbidden detail")).not.toBeInTheDocument();
  });

  it("positive: 동의 내역이 실제로 비어 있으면(에러 없이 consents=[]) 빈 상태를 보여준다", async () => {
    statusResult = {
      data: { tenantId: "tenant-1", consents: [], asOf: "2026-09-09T00:00:00Z" },
      isLoading: false,
      isError: false,
      error: null,
      refetch: refetchStatus,
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("등록된 동의 내역이 없습니다.")).toBeInTheDocument());
  });
});

describe("TrustPage 동의 철회(revoke)", () => {
  it("동의 철회 버튼을 누르면 consentId로 mutate를 호출한다(낙관적 갱신 아님 — onSuccess가 재조회를 트리거)", () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "동의 철회" }));

    expect(revokeConsentMutate).toHaveBeenCalledWith("consent-1", expect.anything());
  });

  it("negative: 교차 테넌트 404(RESOURCE_NOT_FOUND)는 빈 상태가 아니라 명시적 오류로 보여준다", async () => {
    revokeConsentMutate.mockImplementation((_id, opts) => {
      opts?.onError?.(new ApiError(404, "raw not found detail", "trace-tr-3", "RESOURCE_NOT_FOUND"));
    });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "동의 철회" }));

    await waitFor(() => expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument());
    expect(screen.queryByText("raw not found detail")).not.toBeInTheDocument();
  });
});

describe("TrustPage 멤버십 부여(grant)", () => {
  it("subject_id·역할을 입력하고 부여를 누르면 grantMembership을 호출한다", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("부여 대상 subject_id"), {
      target: { value: "subject-9" },
    });
    fireEvent.click(screen.getByRole("button", { name: "부여" }));

    expect(grantMembershipMutate).toHaveBeenCalledWith(
      { subjectId: "subject-9", role: "MEMBER" },
      expect.anything(),
    );
  });

  it("subject_id 없이는 부여 버튼이 비활성화된다", () => {
    renderPage();

    expect(screen.getByRole("button", { name: "부여" })).toBeDisabled();
  });
});

describe("TrustPage 멤버십 정지·폐기 — 서버 거부 사유를 그대로 보여준다(전이표 미복제)", () => {
  it("정지 버튼은 항상 노출되고, 눌렀을 때 서버 응답을 결과 카드로 보여준다", () => {
    suspendMembershipMutate.mockImplementation((_id, opts) => {
      opts?.onSuccess?.({
        membershipId: "membership-1",
        tenantId: "tenant-1",
        subjectId: "subject-1",
        role: "MEMBER",
        state: "SUSPENDED",
        revision: 2,
      });
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("정지·폐기 대상 subject_id"), {
      target: { value: "subject-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "정지(suspend)" }));

    expect(suspendMembershipMutate).toHaveBeenCalledWith("subject-1", expect.anything());
    expect(screen.getByText("subject-1")).toBeInTheDocument();
  });

  it("negative: 마지막 소유자 폐기 거부(403 POLICY_*)는 classifyMembershipError 문구를 보여준다(err.message 미노출)", async () => {
    revokeMembershipMutate.mockImplementation((_id, opts) => {
      opts?.onError?.(
        new ApiError(403, "raw last owner denial detail", "trace-tr-4", "POLICY_RISK_DENIED"),
      );
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("정지·폐기 대상 subject_id"), {
      target: { value: "subject-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "폐기(revoke)" }));

    await waitFor(() =>
      expect(
        screen.getByText("테넌트에는 활성 소유자(OWNER)가 최소 1명 있어야 합니다."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw last owner denial detail")).not.toBeInTheDocument();
  });

  it("negative: 교차 테넌트 거부(403 AUTH_TENANT_MISMATCH)는 ForbiddenNotice로 위임한다(MembersPage.tsx와 동일 순서)", async () => {
    suspendMembershipMutate.mockImplementation((_id, opts) => {
      opts?.onError?.(new ApiError(403, "raw tenant mismatch detail", "trace-tr-5", "AUTH_TENANT_MISMATCH"));
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("정지·폐기 대상 subject_id"), {
      target: { value: "other-tenant-subject" },
    });
    fireEvent.click(screen.getByRole("button", { name: "정지(suspend)" }));

    await waitFor(() =>
      expect(screen.getByText("이 리소스에 접근할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw tenant mismatch detail")).not.toBeInTheDocument();
  });

  it("negative: 404(already_revoked, 교차 테넌트로 위장된 not-found 포함)는 빈 상태가 아니라 명시적 문구로 보여준다", async () => {
    revokeMembershipMutate.mockImplementation((_id, opts) => {
      opts?.onError?.(new ApiError(404, "raw not found detail", "trace-tr-6", "RESOURCE_NOT_FOUND"));
    });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("정지·폐기 대상 subject_id"), {
      target: { value: "subject-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "폐기(revoke)" }));

    await waitFor(() =>
      expect(screen.getByText("이미 폐기되었거나 존재하지 않는 멤버십입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw not found detail")).not.toBeInTheDocument();
  });
});
