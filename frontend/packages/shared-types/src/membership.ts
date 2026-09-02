// L4_platform_observability_tenancy_api_v1.0.md §3.5 테넌트 계약 —
// MembershipView(membership_id/tenant_id/subject_id/role/state/revision/
// created_at/updated_at/schema_version) 파서 + 역할·상태 기반 UI 권한 파생.
// 서버 계약에서 role은 str이지 enum이 아니므로(§3.5: "role을 str에서 enum으로
// 바꾸지 않는다") 파서가 화이트리스트로 좁힌다. 멤버십 목록 API(PLT-29
// trust_memberships)는 서버 미구현이므로 이 파일은 네트워크·저장소를 건드리지
// 않는다 — 계약 파서와 권한 파생 순수함수만 둔다(이 leaf의 decision).

const MEMBERSHIP_ROLES = ["OWNER", "ADMIN", "MEMBER", "AUDITOR", "SERVICE"] as const;
export type MembershipRole = (typeof MEMBERSHIP_ROLES)[number];

const MEMBERSHIP_STATES = ["ACTIVE", "SUSPENDED", "REVOKED"] as const;
export type MembershipState = (typeof MEMBERSHIP_STATES)[number];

export interface MembershipView {
  membershipId: string;
  tenantId: string;
  subjectId: string;
  role: MembershipRole;
  state: MembershipState;
  revision: number;
  createdAt: string;
  updatedAt: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isMembershipRole(value: unknown): value is MembershipRole {
  return typeof value === "string" && (MEMBERSHIP_ROLES as readonly string[]).includes(value);
}

function isMembershipState(value: unknown): value is MembershipState {
  return typeof value === "string" && (MEMBERSHIP_STATES as readonly string[]).includes(value);
}

// throw 금지 — 필드 누락·미지 role/state·schema_version!=="v1"이면 null만
// 반환한다(호출부가 §3.5 계약 위반을 "파싱 실패"로 처리할 수 있게).
export function parseMembershipView(raw: unknown): MembershipView | null {
  if (!isRecord(raw)) return null;

  const {
    membership_id,
    tenant_id,
    subject_id,
    role,
    state,
    revision,
    created_at,
    updated_at,
    schema_version,
  } = raw;

  if (!isNonEmptyString(membership_id)) return null;
  if (!isNonEmptyString(tenant_id)) return null;
  if (!isNonEmptyString(subject_id)) return null;
  if (!isMembershipRole(role)) return null;
  if (!isMembershipState(state)) return null;
  if (typeof revision !== "number" || !Number.isFinite(revision)) return null;
  if (!isNonEmptyString(created_at)) return null;
  if (!isNonEmptyString(updated_at)) return null;
  if (schema_version !== "v1") return null;

  return {
    membershipId: membership_id,
    tenantId: tenant_id,
    subjectId: subject_id,
    role,
    state,
    revision,
    createdAt: created_at,
    updatedAt: updated_at,
  };
}

export interface MembershipCapabilities {
  readonly canView: boolean;
  readonly canTrade: boolean;
  readonly canManageMembers: boolean;
}

const NO_CAPABILITIES: MembershipCapabilities = {
  canView: false,
  canTrade: false,
  canManageMembers: false,
};

// ACTIVE 상태에서 역할별 권한. AUDITOR는 읽기전용(canTrade/canManageMembers
// false), canManageMembers는 OWNER만 true — 나머지는 DoD가 명시하지 않은
// 조합이라 최소권한 원칙으로 false를 기본값으로 둔다.
const ACTIVE_CAPABILITIES: Record<MembershipRole, MembershipCapabilities> = {
  OWNER: { canView: true, canTrade: true, canManageMembers: true },
  ADMIN: { canView: true, canTrade: true, canManageMembers: false },
  MEMBER: { canView: true, canTrade: true, canManageMembers: false },
  AUDITOR: { canView: true, canTrade: false, canManageMembers: false },
  SERVICE: { canView: true, canTrade: true, canManageMembers: false },
};

// SUSPENDED/REVOKED는 role과 무관하게 전부 false(§3.5 DoD) — ACTIVE일 때만
// 역할별 매트릭스를 적용한다.
export function deriveCapabilities(view: MembershipView): MembershipCapabilities {
  if (view.state !== "ACTIVE") return NO_CAPABILITIES;
  return ACTIVE_CAPABILITIES[view.role];
}
