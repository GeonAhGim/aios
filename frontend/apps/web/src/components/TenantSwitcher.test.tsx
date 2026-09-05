import "@testing-library/jest-dom/vitest";
import { AiosApiClient } from "@aios/api-client";
import { classifyForbidden, type MembershipCapabilities, type MembershipView } from "@aios/shared-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTenant } from "../hooks/useTenant";
import { ForbiddenNotice } from "./ForbiddenNotice";
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
  vi.unstubAllGlobals();
  const { result } = renderHook(() => useTenant());
  act(() => {
    result.current.setActiveTenant(null);
  });
});

// task-1158 §3.5 실배선 회귀 가드용 fetch 스텁 — 호출마다 새 Response를 만드는
// factory(공유 Response 재사용 시 Body has already been read). 기본 200 봉투.
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stubFetch(): ReturnType<typeof vi.fn> {
  const fetchMock = vi
    .fn()
    .mockImplementation(() => Promise.resolve(jsonResponse(200, { data: { id: "u-1" }, meta: {} })));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentHeaders(fetchMock: ReturnType<typeof vi.fn>, callIndex = 0): Headers {
  const [, init] = fetchMock.mock.calls[callIndex] as [string, RequestInit];
  return new Headers(init.headers);
}

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

describe("TenantSwitcher — §3.5 실배선 회귀 가드(task-1158)", () => {
  it("전환하면 실 AiosApiClient 요청에 X-Tenant-Id가 붙고, PERSONAL로 되돌리면 헤더가 빠진다", async () => {
    const fetchMock = stubFetch();
    const client = new AiosApiClient("https://api.example.test", () => "token");
    renderSwitcher([membership({ tenantId: TENANT_A })], new QueryClient());

    await client.getMe();
    expect(sentHeaders(fetchMock, 0).has("X-Tenant-Id")).toBe(false);

    selectTenant(TENANT_A);
    await client.getMe();
    expect(sentHeaders(fetchMock, 1).get("X-Tenant-Id")).toBe(TENANT_A);

    selectTenant("PERSONAL");
    await client.getMe();
    expect(sentHeaders(fetchMock, 2).has("X-Tenant-Id")).toBe(false);
  });

  it("실 403 AUTH_TENANT_MISMATCH → handleForbidden 폴백 → 선택기는 PERSONAL, 화면은 ForbiddenNotice(step-up 버튼 없음)", async () => {
    stubFetch().mockImplementationOnce(() =>
      Promise.resolve(
        jsonResponse(403, { error_code: "AUTH_TENANT_MISMATCH", message: "mismatch", trace_id: "t-1158" }),
      ),
    );
    const client = new AiosApiClient("https://api.example.test", () => "token");
    const { rerender } = renderSwitcher([membership({ tenantId: TENANT_A })], new QueryClient());
    selectTenant(TENANT_A);

    const err = await client.getMe().catch((e: unknown) => e);
    expect(classifyForbidden(err)).toBe("tenant_mismatch");

    const { result } = renderHook(() => useTenant());
    act(() => {
      expect(result.current.handleForbidden(err)).toEqual({ previousTenantId: TENANT_A });
    });

    expect((screen.getByLabelText("활성 테넌트") as HTMLSelectElement).value).toBe("PERSONAL");
    expect(screen.getByText("개인")).toBeInTheDocument();

    rerender(<ForbiddenNotice error={err} />);
    expect(screen.getByText("이 리소스에 접근할 권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "step-up 인증" })).not.toBeInTheDocument();
  });

  it("PERSONAL로 되돌릴 때도 캐시를 무효화한다(테넌트 경계를 넘는 잔존 방지, task-475 고정)", () => {
    const queryClient = new QueryClient();
    renderSwitcher([membership({ tenantId: TENANT_A })], queryClient);
    selectTenant(TENANT_A);
    queryClient.setQueryData(["portfolio"], { balance: 1 });
    expect(queryClient.getQueryState(["portfolio"])?.isInvalidated).toBe(false);

    selectTenant("PERSONAL");

    expect(queryClient.getQueryState(["portfolio"])?.isInvalidated).toBe(true);
  });

  it("negative: 같은 테넌트를 다시 선택하면 캐시를 무효화하지 않는다", () => {
    const queryClient = new QueryClient();
    renderSwitcher([membership({ tenantId: TENANT_A })], queryClient);
    selectTenant(TENANT_A);
    queryClient.setQueryData(["portfolio"], { balance: 1 });

    selectTenant(TENANT_A);

    expect(queryClient.getQueryState(["portfolio"])?.isInvalidated).toBe(false);
  });
});
