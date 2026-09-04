import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTenant } from "../../hooks/useTenant";
import { AppShell } from "./AppShell";

// task-1197: §3.5 권한 메뉴 fail-closed 회귀. AppShell 코드를 읽어 보면 현재
// 테넌트 역량(canManageMembers 등)으로 게이팅되는 nav 항목은 아직 없다 —
// 유일한 권한 게이트는 플랫폼 전역 me.isPlatformAdmin 기반 "관리자" 링크뿐이다
// (테넌트별 멤버 관리 게이팅은 MembersPage.tsx가 화면 내부에서 개별적으로 한다).
// 그래서 이 파일은 그 유일한 게이트가 fail-closed인지, 그리고 useTenant(task-455)
// 활성 테넌트 전환과 우연히 결합되어 새는 경로가 없는지를 검증한다.
let meData: { email?: string; isPlatformAdmin?: boolean } | undefined = {
  email: "user@example.com",
  isPlatformAdmin: false,
};

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: meData }),
  useLogout: () => vi.fn(),
}));

const VALID_TENANT_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6";

function tree() {
  return (
    <MemoryRouter initialEntries={["/dashboard"]}>
      <AppShell>
        <div>PAGE_CONTENT</div>
      </AppShell>
    </MemoryRouter>
  );
}

afterEach(() => {
  cleanup();
  meData = { email: "user@example.com", isPlatformAdmin: false };
  // useTenant.ts는 모듈 스코프 싱글턴 스토어다(useTenant.test.ts와 동일 격리 패턴) —
  // 남은 활성 테넌트가 다음 테스트로 새지 않도록 personal로 되돌린다.
  const { result } = renderHook(() => useTenant());
  act(() => {
    result.current.setActiveTenant(null);
  });
});

describe("AppShell — §3.5 권한 메뉴 fail-closed", () => {
  it("플랫폼 관리자가 아니면 관리자 메뉴가 렌더되지 않는다", () => {
    render(tree());

    expect(screen.queryByText("관리자")).not.toBeInTheDocument();
    expect(screen.getByText("PAGE_CONTENT")).toBeInTheDocument();
  });

  it("me 조회 결과가 아직 없으면(로딩 중과 동일) 관리자 메뉴를 노출하지 않는다(fail-closed)", () => {
    meData = undefined;
    render(tree());

    expect(screen.queryByText("관리자")).not.toBeInTheDocument();
  });

  it("플랫폼 관리자면 관리자 메뉴가 렌더된다", () => {
    meData = { email: "admin@example.com", isPlatformAdmin: true };
    render(tree());

    expect(screen.getByText("관리자")).toBeInTheDocument();
  });

  it("negative: 비관리자 상태에서 활성 테넌트를 전환해도 관리자 메뉴는 계속 감춰진다(테넌트 상태와 결합된 누수가 없다)", () => {
    const { rerender } = render(tree());
    expect(screen.queryByText("관리자")).not.toBeInTheDocument();

    const { result } = renderHook(() => useTenant());
    act(() => {
      const accepted = result.current.setActiveTenant(VALID_TENANT_ID);
      expect(accepted).toBe(true);
    });
    rerender(tree());

    expect(screen.queryByText("관리자")).not.toBeInTheDocument();
  });

  it("negative: 관리자 상태에서 활성 테넌트를 전환해도 관리자 메뉴는 계속 보인다(테넌트 상태와 결합되어 사라지지 않는다)", () => {
    meData = { email: "admin@example.com", isPlatformAdmin: true };
    const { rerender } = render(tree());
    expect(screen.getByText("관리자")).toBeInTheDocument();

    const { result } = renderHook(() => useTenant());
    act(() => {
      result.current.setActiveTenant(VALID_TENANT_ID);
    });
    rerender(tree());

    expect(screen.getByText("관리자")).toBeInTheDocument();
  });
});
