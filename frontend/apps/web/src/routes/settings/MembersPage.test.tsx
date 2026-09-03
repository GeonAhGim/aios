import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { useTenant } from "../../hooks/useTenant";
import { MembersPage, type MembersPageProps } from "./MembersPage";

const TENANT_A = "3fa85f64-5717-4562-b3fc-2c963f66afa6";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { userId: "me-1", email: "me-1@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

// useTenant.ts는 모듈 스코프 싱글턴 스토어다 — TenantSwitcher.test.tsx와 동일하게
// 각 테스트 뒤 personal(null)로 되돌려 격리한다.
afterEach(() => {
  const { result } = renderHook(() => useTenant());
  act(() => {
    result.current.setActiveTenant(null);
  });
});

function rawMember(overrides: Record<string, unknown> = {}) {
  return {
    membership_id: "membership-1",
    tenant_id: TENANT_A,
    subject_id: "subject-1",
    role: "MEMBER",
    state: "ACTIVE",
    revision: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-02T00:00:00Z",
    schema_version: "v1",
    ...overrides,
  };
}

function renderPage(props: MembersPageProps = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/settings/members"]}>
        <Routes>
          <Route path="/settings/members" element={<MembersPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
}

function activateTenant(tenantId: string) {
  const { result } = renderHook(() => useTenant());
  act(() => {
    result.current.setActiveTenant(tenantId);
  });
}

describe("MembersPage", () => {
  it("활성 테넌트가 없으면(personal) 멤버 목록을 조회하지 않고 안내만 보여준다", () => {
    const fetchMembers = vi.fn();
    renderPage({ fetchMembers });

    expect(screen.getByText("조직/가구 테넌트를 선택하면 멤버를 관리할 수 있습니다.")).toBeInTheDocument();
    expect(fetchMembers).not.toHaveBeenCalled();
  });

  it("활성 테넌트의 멤버 목록을 role/state 배지와 함께 렌더한다", async () => {
    activateTenant(TENANT_A);
    renderPage({
      currentUserId: "me-1",
      fetchMembers: async () => [
        rawMember({ membership_id: "m-1", subject_id: "me-1", role: "OWNER" }),
        rawMember({ membership_id: "m-2", subject_id: "subject-2", role: "MEMBER", state: "SUSPENDED" }),
      ],
    });

    await waitFor(() => expect(screen.getByText("me-1")).toBeInTheDocument());
    // 역할 라벨("소유자"/"멤버")은 초대 폼의 역할 Select 옵션과도 문구가 겹치므로
    // 멤버 목록(<ul>)으로 범위를 좁혀 배지 렌더만 확인한다.
    const list = screen.getByRole("list");
    expect(within(list).getByText("소유자")).toBeInTheDocument();
    expect(within(list).getByText("subject-2")).toBeInTheDocument();
    expect(within(list).getByText("멤버")).toBeInTheDocument();
    // "정지"는 상태 배지 문구이자 정지 버튼 라벨이기도 하다 — 두 행(버튼 2개) +
    // SUSPENDED 배지 1개 = 총 3개가 나와야 배지가 실제로 렌더된 것이다.
    expect(screen.getAllByText("정지")).toHaveLength(3);
  });

  it("OWNER 권한 보유자는 초대·정지·폐기 버튼을 사용할 수 있다", async () => {
    activateTenant(TENANT_A);
    const grant = vi.fn().mockResolvedValue(undefined);
    renderPage({
      currentUserId: "me-1",
      fetchMembers: async () => [
        rawMember({ membership_id: "m-1", subject_id: "me-1", role: "OWNER" }),
        rawMember({ membership_id: "m-2", subject_id: "subject-2", role: "MEMBER" }),
      ],
      membershipsClient: { grant, suspend: vi.fn(), revoke: vi.fn() },
    });

    await waitFor(() => expect(screen.getByText("subject-2")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "초대" })).not.toBeDisabled());
    const suspendButtons = screen.getAllByRole("button", { name: "정지" });
    expect(suspendButtons.some((b) => !b.hasAttribute("disabled"))).toBe(true);
  });

  it("negative: 권한 없는 사용자(MEMBER)는 초대·정지·폐기 버튼이 전부 비활성이다", async () => {
    activateTenant(TENANT_A);
    renderPage({
      currentUserId: "me-1",
      fetchMembers: async () => [
        rawMember({ membership_id: "m-1", subject_id: "me-1", role: "MEMBER" }),
        rawMember({ membership_id: "m-2", subject_id: "subject-2", role: "MEMBER" }),
      ],
    });

    await waitFor(() => expect(screen.getByText("subject-2")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "초대" })).toBeDisabled();
    for (const button of screen.getAllByRole("button", { name: "정지" })) {
      expect(button).toBeDisabled();
    }
    for (const button of screen.getAllByRole("button", { name: "폐기" })) {
      expect(button).toBeDisabled();
    }
  });

  it("negative: last-owner revoke 거부(409 STATE_INVALID_TRANSITION)를 고정 문구로 노출하고 err.message를 보여주지 않는다", async () => {
    activateTenant(TENANT_A);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const revoke = vi
      .fn()
      .mockRejectedValue(new ApiError(409, "raw server detail should not leak", "trace-1", "STATE_INVALID_TRANSITION"));

    renderPage({
      currentUserId: "me-1",
      fetchMembers: async () => [rawMember({ membership_id: "m-1", subject_id: "me-1", role: "OWNER" })],
      membershipsClient: { grant: vi.fn(), suspend: vi.fn(), revoke },
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "폐기" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "폐기" }));

    await waitFor(() =>
      expect(screen.getByText("테넌트에는 활성 소유자(OWNER)가 최소 1명 있어야 합니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail should not leak")).not.toBeInTheDocument();

    vi.restoreAllMocks();
  });

  it("negative: last-owner suspend 거부(403 POLICY_LAST_OWNER)도 같은 고정 문구를 노출한다(ForbiddenNotice 제네릭 문구·원본 코드 노출 금지)", async () => {
    activateTenant(TENANT_A);
    const suspend = vi
      .fn()
      .mockRejectedValue(new ApiError(403, "raw server detail should not leak", "trace-3", "POLICY_LAST_OWNER"));

    renderPage({
      currentUserId: "me-1",
      fetchMembers: async () => [rawMember({ membership_id: "m-1", subject_id: "me-1", role: "OWNER" })],
      membershipsClient: { grant: vi.fn(), suspend, revoke: vi.fn() },
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "정지" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "정지" }));

    await waitFor(() =>
      expect(screen.getByText("테넌트에는 활성 소유자(OWNER)가 최소 1명 있어야 합니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("POLICY_LAST_OWNER")).not.toBeInTheDocument();
    expect(screen.queryByText("raw server detail should not leak")).not.toBeInTheDocument();
  });

  it("negative: cross-tenant 403(AUTH_TENANT_MISMATCH)을 ForbiddenNotice로 노출한다", async () => {
    activateTenant(TENANT_A);
    const suspend = vi
      .fn()
      .mockRejectedValue(new ApiError(403, "raw detail", "trace-2", "AUTH_TENANT_MISMATCH"));

    renderPage({
      currentUserId: "me-1",
      fetchMembers: async () => [rawMember({ membership_id: "m-1", subject_id: "me-1", role: "OWNER" })],
      membershipsClient: { grant: vi.fn(), suspend, revoke: vi.fn() },
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "정지" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "정지" }));

    await waitFor(() => expect(screen.getByText("이 리소스에 접근할 권한이 없습니다.")).toBeInTheDocument());
    expect(suspend).toHaveBeenCalledWith("m-1");
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });
});
