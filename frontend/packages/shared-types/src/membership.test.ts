import { describe, expect, it } from "vitest";
import {
  deriveCapabilities,
  parseMembershipView,
  type MembershipCapabilities,
  type MembershipRole,
  type MembershipState,
  type MembershipView,
} from "./membership";

function validRaw(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    membership_id: "membership-1",
    tenant_id: "tenant-1",
    subject_id: "subject-1",
    role: "OWNER",
    state: "ACTIVE",
    revision: 1,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-02T00:00:00Z",
    schema_version: "v1",
    ...overrides,
  };
}

describe("parseMembershipView", () => {
  it("정상 §3.5 MembershipView를 camelCase로 파싱한다", () => {
    expect(parseMembershipView(validRaw())).toEqual({
      membershipId: "membership-1",
      tenantId: "tenant-1",
      subjectId: "subject-1",
      role: "OWNER",
      state: "ACTIVE",
      revision: 1,
      createdAt: "2026-09-01T00:00:00Z",
      updatedAt: "2026-09-02T00:00:00Z",
    });
  });

  it.each([
    "membership_id",
    "tenant_id",
    "subject_id",
    "role",
    "state",
    "revision",
    "created_at",
    "updated_at",
    "schema_version",
  ])("%s 필드가 누락되면 null이다(throw 금지)", (field) => {
    const raw = validRaw();
    delete raw[field];
    expect(parseMembershipView(raw)).toBeNull();
  });

  it("미지 role 값이면 null이다", () => {
    expect(parseMembershipView(validRaw({ role: "SUPERADMIN" }))).toBeNull();
  });

  it("미지 state 값이면 null이다", () => {
    expect(parseMembershipView(validRaw({ state: "PENDING" }))).toBeNull();
  });

  it("schema_version이 'v1'이 아니면 null이다", () => {
    expect(parseMembershipView(validRaw({ schema_version: "v2" }))).toBeNull();
  });

  it("revision이 숫자가 아니면 null이다", () => {
    expect(parseMembershipView(validRaw({ revision: "1" }))).toBeNull();
  });

  it("data가 객체가 아니면 null이다", () => {
    expect(parseMembershipView(null)).toBeNull();
    expect(parseMembershipView("membership")).toBeNull();
  });
});

const ROLES: MembershipRole[] = ["OWNER", "ADMIN", "MEMBER", "AUDITOR", "SERVICE"];
const STATES: MembershipState[] = ["ACTIVE", "SUSPENDED", "REVOKED"];

const EXPECTED_ACTIVE: Record<MembershipRole, MembershipCapabilities> = {
  OWNER: { canView: true, canTrade: true, canManageMembers: true },
  ADMIN: { canView: true, canTrade: true, canManageMembers: false },
  MEMBER: { canView: true, canTrade: true, canManageMembers: false },
  AUDITOR: { canView: true, canTrade: false, canManageMembers: false },
  SERVICE: { canView: true, canTrade: true, canManageMembers: false },
};

const ALL_FALSE: MembershipCapabilities = {
  canView: false,
  canTrade: false,
  canManageMembers: false,
};

function membershipOf(role: MembershipRole, state: MembershipState): MembershipView {
  return {
    membershipId: "membership-1",
    tenantId: "tenant-1",
    subjectId: "subject-1",
    role,
    state,
    revision: 1,
    createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-02T00:00:00Z",
  };
}

describe("deriveCapabilities", () => {
  const cases: Array<[MembershipRole, MembershipState, MembershipCapabilities]> = ROLES.flatMap(
    (role) =>
      STATES.map((state): [MembershipRole, MembershipState, MembershipCapabilities] => [
        role,
        state,
        state === "ACTIVE" ? EXPECTED_ACTIVE[role] : ALL_FALSE,
      ]),
  );

  it("5역할 × 3상태 = 15조합 전수 고정", () => {
    expect(cases).toHaveLength(15);
  });

  it.each(cases)("role=%s state=%s -> %o", (role, state, expected) => {
    expect(deriveCapabilities(membershipOf(role, state))).toEqual(expected);
  });

  it("AUDITOR는 ACTIVE여도 읽기전용이다(canTrade/canManageMembers false, canView true)", () => {
    const caps = deriveCapabilities(membershipOf("AUDITOR", "ACTIVE"));
    expect(caps).toEqual({ canView: true, canTrade: false, canManageMembers: false });
  });

  it("OWNER만 canManageMembers true다", () => {
    for (const role of ROLES) {
      const caps = deriveCapabilities(membershipOf(role, "ACTIVE"));
      expect(caps.canManageMembers).toBe(role === "OWNER");
    }
  });

  it("SUSPENDED/REVOKED는 역할과 무관하게 전부 false다", () => {
    for (const role of ROLES) {
      expect(deriveCapabilities(membershipOf(role, "SUSPENDED"))).toEqual(ALL_FALSE);
      expect(deriveCapabilities(membershipOf(role, "REVOKED"))).toEqual(ALL_FALSE);
    }
  });
});
