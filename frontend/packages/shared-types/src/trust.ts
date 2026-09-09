// src/foundation/trust/contracts/v1.py(ConsentDecision/ConsentState) +
// src/api/routers/foundation/trust_memberships.py(MembershipResponse) 1:1 대응.
// task-2338(FE-OPS-4).
//
// reconciliation.ts/evidence.ts(task-2337)와 동일하게 런타임 파서를 두지 않는다 —
// ApiClientBase.postEnvelope/requestByRoute가 이미 keysToCamel로 응답을 변환해
// 돌려주므로 타입만 옮긴다.
//
// MembershipResponse(trust_memberships.py:35-41)는 membership_id/tenant_id/
// subject_id/role/state/revision 6필드뿐이다 — created_at/updated_at/schema_version이
// 없다. 이는 §3.5 테넌트 멤버십 계약(membership.ts MembershipView, task-474/1159)과는
// 다른 bounded context라 그 파서(parseMembershipView)를 그대로 쓸 수 없다(그 파서는
// 저 3필드를 요구해서 실서버 응답을 항상 파싱 실패로 판정하게 된다) — decision(§C):
// role/state 리터럴 유니온(MembershipRole/MembershipState)만 membership.ts에서
// 재사용하고, 이 파일의 TrustMembershipView는 실제 응답 필드만 갖는다.
import type { MembershipRole, MembershipState } from "./membership";

export type ConsentState = "NONE" | "ACTIVE" | "REVOKED";

export interface ConsentDecisionView {
  consentId: string;
  tenantId: string;
  purpose: string;
  disclosureId: string;
  disclosureRevision: number;
  state: ConsentState;
  acceptedAt: string | null;
  revokedAt: string | null;
  expiresAt: string | null;
  schemaVersion: string;
}

export interface TrustStatusView {
  tenantId: string;
  consents: ConsentDecisionView[];
  asOf: string;
}

export interface TrustMembershipView {
  membershipId: string;
  tenantId: string;
  subjectId: string;
  role: MembershipRole;
  state: MembershipState;
  revision: number;
}

export interface GrantMembershipBody {
  subjectId: string;
  role: MembershipRole;
}
