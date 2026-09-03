// spec §3.5 테넌트 계약 + §9 PLT-28. 활성 테넌트 선택기 — 상태 저장·헤더
// 배선은 task-455(tenantContext.ts/useTenant.ts)를 그대로 재사용하고, 이
// 컴포넌트는 (1) PERSONAL을 포함한 선택 UI, (2) 전환 시 react-query 캐시
// 무효화, (3) task-474 deriveCapabilities 기반 역할 배지·권한 통지만 더한다.
//
// 멤버십 목록 API(PLT-29 trust_memberships)는 서버 미구현이므로 이 컴포넌트는
// 네트워크를 호출하지 않는다 — memberships는 호출자가 주입하는 props다
// (이 leaf의 decision). 새 테넌트 스토어·새 헤더 주입 경로도 만들지 않는다.
import {
  deriveCapabilities,
  type MembershipCapabilities,
  type MembershipRole,
  type MembershipView,
} from "@aios/shared-types";
import { Badge, Select } from "@aios/ui-web";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, type ChangeEvent } from "react";
import { useTenant } from "../hooks/useTenant";

const ROLE_LABELS: Record<MembershipRole, string> = {
  OWNER: "소유자",
  ADMIN: "관리자",
  MEMBER: "멤버",
  AUDITOR: "감사자(읽기전용)",
  SERVICE: "서비스",
};

// personal tenant는 멤버십 레코드가 없다 — 본인 소유이므로 전권한으로 취급한다.
const PERSONAL_CAPABILITIES: MembershipCapabilities = {
  canView: true,
  canTrade: true,
  canManageMembers: true,
};

// 선택된 tenant_id에 대응하는 멤버십을 props에서 찾지 못하면(데이터 정합성
// 문제·아직 로딩 전) 최소권한 원칙으로 전부 false 처리한다.
const UNKNOWN_CAPABILITIES: MembershipCapabilities = {
  canView: false,
  canTrade: false,
  canManageMembers: false,
};

export interface TenantSwitcherProps {
  /** PLT-29 서버 미구현이라 호출자가 주입하는 멤버십 목록. */
  memberships: MembershipView[];
  /**
   * 활성 테넌트가 바뀔 때마다(마운트 시 최초 1회 포함) 파생된 권한을 알려준다.
   * 호출자는 이 값으로 자신의 쓰기 버튼(예: 설정 저장)을 게이팅한다 —
   * AUDITOR·SUSPENDED/REVOKED 등은 canTrade가 false로 내려온다.
   */
  onCapabilitiesChange?: (capabilities: MembershipCapabilities) => void;
}

export function TenantSwitcher({ memberships, onCapabilitiesChange }: TenantSwitcherProps) {
  const { activeTenantId, setActiveTenant } = useTenant();
  const queryClient = useQueryClient();

  const activeMembership = useMemo(
    () => (activeTenantId ? (memberships.find((m) => m.tenantId === activeTenantId) ?? null) : null),
    [activeTenantId, memberships],
  );

  const capabilities: MembershipCapabilities =
    activeTenantId === null
      ? PERSONAL_CAPABILITIES
      : activeMembership
        ? deriveCapabilities(activeMembership)
        : UNKNOWN_CAPABILITIES;

  useEffect(() => {
    onCapabilitiesChange?.(capabilities);
    // capabilities는 activeTenantId·activeMembership에서 파생되므로 그 둘만
    // 의존성으로 두면 충분하다 — onCapabilitiesChange까지 넣으면 부모가
    // 매 렌더 새 함수를 넘길 때 무한 루프가 될 수 있다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTenantId, activeMembership]);

  function handleChange(event: ChangeEvent<HTMLSelectElement>) {
    const value = event.target.value;
    const nextTenantId = value === "PERSONAL" ? null : value;
    if (nextTenantId === activeTenantId) return;

    const accepted = setActiveTenant(nextTenantId);
    if (!accepted) return;

    // 이전 테넌트 데이터가 화면에 남지 않도록 전체 쿼리 캐시를 무효화한다 —
    // useLogout.ts의 qc.clear()와 같은 이유(테넌트 경계를 넘는 상태 잔존 방지).
    queryClient.invalidateQueries();
  }

  return (
    <div className="flex items-center gap-3">
      <Select
        value={activeTenantId ?? "PERSONAL"}
        onChange={handleChange}
        aria-label="활성 테넌트"
        className="w-auto"
      >
        <option value="PERSONAL">개인(PERSONAL)</option>
        {memberships.map((m) => (
          <option key={m.tenantId} value={m.tenantId}>
            {m.tenantId}
          </option>
        ))}
      </Select>
      <Badge tone={activeTenantId === null ? "accent" : "neutral"}>
        {activeTenantId === null
          ? "개인"
          : activeMembership
            ? ROLE_LABELS[activeMembership.role]
            : "알 수 없음"}
      </Badge>
    </div>
  );
}
