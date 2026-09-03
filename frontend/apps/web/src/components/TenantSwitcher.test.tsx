import "@testing-library/jest-dom/vitest";
import type { MembershipCapabilities, MembershipView } from "@aios/shared-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTenant } from "../hooks/useTenant";
import { TenantSwitcher } from "./TenantSwitcher";

const TENANT_A = "3fa85f64-5717-4562-b3fc-2c963f66afa6";
const TENANT_B = "9f8e7d6c-1234-4562-b3fc-2c963f66afa6";

function membership(overrides: Partial<MembershipView> = {}): MembershipView {
  return {
    membershipId: "membership-1",
    tenantId: TENANT_A,
    subjectId: "subject-1",
    role: "MEMBER",
    state: "ACTIVE",
    revision: 1,
    createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-02T00:00:00Z",
    ...overrides,
  };
}

function renderSwitcher(
  memberships: MembershipView[],
  queryClient: QueryClient,
  onCapabilitiesChange?: (c: MembershipCapabilities) => void,
) {
  function wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  }
  return render(
    <TenantSwitcher memberships={memberships} onCapabilitiesChange={onCapabilitiesChange} />,
    { wrapper },
  );
}

function selectTenant(tenantId: string) {
  act(() => {
    fireEvent.change(screen.getByLabelText("활성 테넌트"), { target: { value: tenantId } });
  });
}

// 모듈 스코프 싱글턴 스토어(useTenant.ts)이므로 각 테스트 뒤 personal로
// 되돌려 격리한다 — useTenant.test.ts와 동일한 패턴.
afterEach(() => {
  cleanup();
  const { result } = renderHook(() => useTenant());
  act(() => {
    result.current.setActiveTenant(null);
  });
});

describe("TenantSwitcher", () => {
  it("memberships가 비어 있어도 PERSONAL은 항상 선택 가능하고 기본값이다", () => {
    renderSwitcher([], new QueryClient());

    const select = screen.getByLabelText("활성 테넌트") as HTMLSelectElement;
    expect(select.value).toBe("PERSONAL");
    expect(screen.getByText("개인")).toBeInTheDocument();
  });

  it("전환 시 react-query 캐시를 무효화해 이전 테넌트 데이터가 남지 않는다", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(["portfolio"], { balance: 100 });
    expect(queryClient.getQueryState(["portfolio"])?.isInvalidated).toBe(false);

    renderSwitcher([membership({ tenantId: TENANT_A })], queryClient);

    selectTenant(TENANT_A);

    // negative: 컴포넌트가 invalidateQueries를 호출하지 않으면 이 값은 계속
    // false로 남아 이 단언이 실패한다.
    expect(queryClient.getQueryState(["portfolio"])?.isInvalidated).toBe(true);
  });

  it("활성 테넌트를 바꾸면 useTenant 스토어의 activeTenantId도 갱신된다", () => {
    renderSwitcher([membership({ tenantId: TENANT_A })], new QueryClient());

    selectTenant(TENANT_A);

    const { result } = renderHook(() => useTenant());
    expect(result.current.activeTenantId).toBe(TENANT_A);
  });

  it("역할 배지가 deriveCapabilities 기반으로 활성 멤버십의 역할을 보여준다", () => {
    renderSwitcher([membership({ tenantId: TENANT_A, role: "ADMIN" })], new QueryClient());

    selectTenant(TENANT_A);

    expect(screen.getByText("관리자")).toBeInTheDocument();
  });

  it("AUDITOR 멤버십으로 전환하면 onCapabilitiesChange가 canTrade=false를 통지한다(쓰기 버튼 비활성화 신호)", () => {
    const onCapabilitiesChange = vi.fn();
    renderSwitcher(
      [membership({ tenantId: TENANT_A, role: "AUDITOR" })],
      new QueryClient(),
      onCapabilitiesChange,
    );

    selectTenant(TENANT_A);

    expect(onCapabilitiesChange).toHaveBeenLastCalledWith({
      canView: true,
      canTrade: false,
      canManageMembers: false,
    });
    expect(screen.getByText("감사자(읽기전용)")).toBeInTheDocument();
  });

  it("MEMBER 멤버십은 canTrade=true를 통지한다(쓰기 버튼 활성 상태 유지)", () => {
    const onCapabilitiesChange = vi.fn();
    renderSwitcher(
      [membership({ tenantId: TENANT_A, role: "MEMBER" })],
      new QueryClient(),
      onCapabilitiesChange,
    );

    selectTenant(TENANT_A);

    expect(onCapabilitiesChange).toHaveBeenLastCalledWith({
      canView: true,
      canTrade: true,
      canManageMembers: false,
    });
  });

  it("마운트 시(personal) onCapabilitiesChange가 전권한을 통지한다", () => {
    const onCapabilitiesChange = vi.fn();
    renderSwitcher([], new QueryClient(), onCapabilitiesChange);

    expect(onCapabilitiesChange).toHaveBeenCalledWith({
      canView: true,
      canTrade: true,
      canManageMembers: true,
    });
  });

  it("negative: 활성 tenant_id가 memberships에서 사라지면(정합성 어긋남) 최소권한(전부 false)으로 재통지한다", () => {
    const onCapabilitiesChange = vi.fn();
    const queryClient = new QueryClient();
    function wrapper({ children }: { children: ReactNode }) {
      return createElement(QueryClientProvider, { client: queryClient }, children);
    }

    const { rerender } = render(
      <TenantSwitcher
        memberships={[membership({ tenantId: TENANT_B, role: "OWNER" })]}
        onCapabilitiesChange={onCapabilitiesChange}
      />,
      { wrapper },
    );

    selectTenant(TENANT_B);
    onCapabilitiesChange.mockClear();

    rerender(<TenantSwitcher memberships={[]} onCapabilitiesChange={onCapabilitiesChange} />);

    expect(onCapabilitiesChange).toHaveBeenLastCalledWith({
      canView: false,
      canTrade: false,
      canManageMembers: false,
    });
    expect(screen.getByText("알 수 없음")).toBeInTheDocument();
  });
});
