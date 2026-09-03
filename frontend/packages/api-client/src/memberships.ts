// L4_platform_observability_tenancy_api_v1.0.md §3.5 테넌트 계약 + §9 PLT-29.
// createMembershipsClient(grant/suspend/revoke)는 ApiClientBase(http.ts)만 거친다 —
// fetch를 직접 호출하지 않고, X-Tenant-Id 헤더도 새로 만들지 않는다.
// ApiClientBase.fetchJson이 이미 모든 요청에 configureTenantHeadersProvider로 주입된
// tenantContext.ts(task-455 createTenantStore)의 tenantHeaders()를 얹으므로(http.ts
// fetchJson), 이 클라이언트는 그 배선을 그대로 상속만 한다 — 중복 구현 금지.
//
// MembershipView 파싱은 task-474(5cda851) parseMembershipView(shared-types/membership.ts)를
// 그대로 재사용한다(새 파서 금지). 다만 parseMembershipView는 서버 원본(snake_case)을
// 기대하는데 ApiClientBase.postEnvelope는 응답을 이미 camelCase로 변환해 돌려준다
// (caseConvert.ts keysToCamel). 그래서 응답을 keysToSnake로 한 번 되돌린 뒤 넘긴다 —
// 새 변환기를 만들지 않고 기존 keysToSnake(caseConvert.ts)를 재사용한다.
//
// 경로: §3.5 라우터 표(문서 87행)의 실제 계약은 `/v1/foundation/trust/memberships`이고
// tenant scope는 URL이 아니라 X-Tenant-Id 헤더로 전달한다. 이 task의 note가 적은
// `/v1/foundation/trust/tenants/{tenantId}/memberships`는 문서 어디에도 나오지 않는
// 경로라 §3.5 원문(87행)을 따랐다 — decision에 따라 이 사실을 task note에 남긴다.
// 서버(PLT-29)가 아직 없어 실제 왕복 검증은 없다(task-454/606 선례대로 계약·클라이언트
// 모듈과 vitest만 둔다).
import { parseMembershipView, type MembershipRole, type MembershipView } from "@aios/shared-types";
import { keysToSnake } from "./caseConvert";
import { ApiClientBase } from "./http";

const MEMBERSHIPS_PATH = "/v1/foundation/trust/memberships";

export interface GrantMembershipBody {
  subjectId: string;
  role: MembershipRole;
}

export class MembershipParseError extends Error {
  constructor() {
    super("서버 응답이 §3.5 MembershipView 계약과 일치하지 않습니다.");
  }
}

function parseOrThrow(raw: unknown): MembershipView {
  const view = parseMembershipView(keysToSnake(raw));
  if (!view) throw new MembershipParseError();
  return view;
}

class MembershipsApiClient extends ApiClientBase {
  grantMembership(body: GrantMembershipBody): Promise<MembershipView> {
    return this.postEnvelope<unknown>(MEMBERSHIPS_PATH, body).then(parseOrThrow);
  }

  suspendMembership(membershipId: string): Promise<MembershipView> {
    return this.postEnvelope<unknown>(`${MEMBERSHIPS_PATH}/${membershipId}:suspend`).then(parseOrThrow);
  }

  revokeMembership(membershipId: string): Promise<MembershipView> {
    return this.postEnvelope<unknown>(`${MEMBERSHIPS_PATH}/${membershipId}:revoke`).then(parseOrThrow);
  }
}

export interface MembershipsClient {
  grant(body: GrantMembershipBody): Promise<MembershipView>;
  suspend(membershipId: string): Promise<MembershipView>;
  revoke(membershipId: string): Promise<MembershipView>;
}

export function createMembershipsClient(baseUrl: string, getToken: () => string | null): MembershipsClient {
  const client = new MembershipsApiClient(baseUrl, getToken);
  return {
    grant: (body) => client.grantMembership(body),
    suspend: (membershipId) => client.suspendMembership(membershipId),
    revoke: (membershipId) => client.revokeMembership(membershipId),
  };
}
