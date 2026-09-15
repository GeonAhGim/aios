// task-2657(AI-22): AiStudioPage.tsx(공급자 설정·토큰·제안 목록·실험 비교·승격
// 버튼 확인 흐름)가 쓰는 클라이언트. follow.ts(task-2699)와 동일 관용 —
// apiRoutes.ts의 ai.*가 유령 경로(implemented=false)라 실제 fetch를 시도하기
// 전에 typed 오류로 단락한다. AiosApiClient 합성(client.ts)에는 얹지 않고
// (positions.ts/follow.ts와 동일 관용) 화면이 createAiClient로 직접 만든다 —
// 라우터가 생기면(src/api/routers/ai.py, AI-17) apiRoutes.ts의 implemented만
// true로 바꾸면 이 단락 없이 그대로 배선된다. GET은 marketplace.ts(searchListings)
// 와 동일한 resolveEnvelope(route) 삼항 관용으로 봉투 분기를 레지스트리에
// 위임한다(apiPaths.clientsScan.test.ts task-1160 가드가 this.requestEnvelope/
// this.request 직접 호출을 하드코딩으로 잡는다 — postEnvelope/putEnvelope는 그
// 가드 대상이 아니라 POST/PUT은 backtests.ts처럼 곧바로 쓴다).
import type {
  AgentTokenView,
  AiProviderSettingView,
  ExperimentView,
  IssueAgentTokenRequest,
  PromoteTicketView,
  StrategyProposalView,
  UpdateAiProviderSettingRequest,
} from "@aios/shared-types";
import { isRouteImplemented, resolveEnvelope, resolvePath, type ApiRouteName } from "../apiPaths";
import { ApiClientBase } from "../http";

type AiRouteName =
  | "ai.providers.base"
  | "ai.providers.item"
  | "ai.tokens.base"
  | "ai.tokens.revoke"
  | "ai.proposals.base"
  | "ai.proposals.promoteTicket"
  | "ai.proposals.promote"
  | "ai.experiments.base";

const NOT_IMPLEMENTED_MESSAGE: Record<AiRouteName, string> = {
  "ai.providers.base": "AI 공급자 설정 API가 아직 제공되지 않습니다.",
  "ai.providers.item": "AI 공급자 설정 변경 API가 아직 제공되지 않습니다.",
  "ai.tokens.base": "AI 에이전트 토큰 API가 아직 제공되지 않습니다.",
  "ai.tokens.revoke": "AI 에이전트 토큰 폐기 API가 아직 제공되지 않습니다.",
  "ai.proposals.base": "AI 제안 목록 API가 아직 제공되지 않습니다.",
  "ai.proposals.promoteTicket": "AI 제안 PAPER 승격 확인 API가 아직 제공되지 않습니다.",
  "ai.proposals.promote": "AI 제안 PAPER 승격 API가 아직 제공되지 않습니다.",
  "ai.experiments.base": "AI 실험 조회 API가 아직 제공되지 않습니다.",
};

// sessions.ts(task-1325)의 SessionsRouteNotImplementedError·follow.ts(task-2699)의
// FollowRouteNotImplementedError와 동일 패턴 — 호출부(AiStudioPage.tsx)가 문자열
// 매칭 대신 instanceof/route 필드로 "아직 없는 라우트"를 판별할 수 있게 한다.
export class AiRouteNotImplementedError extends Error {
  readonly route: ApiRouteName;

  constructor(route: AiRouteName) {
    super(NOT_IMPLEMENTED_MESSAGE[route]);
    this.name = "AiRouteNotImplementedError";
    this.route = route;
  }
}

function assertImplemented(route: AiRouteName): void {
  if (!isRouteImplemented(route)) {
    throw new AiRouteNotImplementedError(route);
  }
}

class AiApiClient extends ApiClientBase {
  async listProviderSettings(): Promise<AiProviderSettingView[]> {
    assertImplemented("ai.providers.base");
    const path = resolvePath("ai.providers.base");
    return resolveEnvelope("ai.providers.base") ? this.requestEnvelope(path) : this.request(path);
  }

  async updateProviderSetting(
    provider: string,
    body: UpdateAiProviderSettingRequest,
  ): Promise<AiProviderSettingView> {
    assertImplemented("ai.providers.item");
    const path = resolvePath("ai.providers.item").replace(":provider", encodeURIComponent(provider));
    return this.putEnvelope(path, body);
  }

  async listAgentTokens(): Promise<AgentTokenView[]> {
    assertImplemented("ai.tokens.base");
    const path = resolvePath("ai.tokens.base");
    return resolveEnvelope("ai.tokens.base") ? this.requestEnvelope(path) : this.request(path);
  }

  async issueAgentToken(body: IssueAgentTokenRequest): Promise<AgentTokenView> {
    assertImplemented("ai.tokens.base");
    return this.postEnvelope(resolvePath("ai.tokens.base"), body);
  }

  async revokeAgentToken(tokenId: string): Promise<void> {
    assertImplemented("ai.tokens.revoke");
    const path = resolvePath("ai.tokens.revoke").replace(":tokenId", encodeURIComponent(tokenId));
    await this.postEnvelope<void>(path);
  }

  async listProposals(): Promise<StrategyProposalView[]> {
    assertImplemented("ai.proposals.base");
    const path = resolvePath("ai.proposals.base");
    return resolveEnvelope("ai.proposals.base") ? this.requestEnvelope(path) : this.request(path);
  }

  async requestPromoteTicket(proposalId: string): Promise<PromoteTicketView> {
    assertImplemented("ai.proposals.promoteTicket");
    const path = resolvePath("ai.proposals.promoteTicket").replace(":proposalId", encodeURIComponent(proposalId));
    return this.postEnvelope(path);
  }

  async confirmPromote(proposalId: string, ticketId: string): Promise<void> {
    assertImplemented("ai.proposals.promote");
    const path = resolvePath("ai.proposals.promote").replace(":proposalId", encodeURIComponent(proposalId));
    await this.postEnvelope<void>(path, { ticketId });
  }

  async listExperiments(): Promise<ExperimentView[]> {
    assertImplemented("ai.experiments.base");
    const path = resolvePath("ai.experiments.base");
    return resolveEnvelope("ai.experiments.base") ? this.requestEnvelope(path) : this.request(path);
  }
}

export interface AiClient {
  listProviderSettings(): Promise<AiProviderSettingView[]>;
  updateProviderSetting(provider: string, body: UpdateAiProviderSettingRequest): Promise<AiProviderSettingView>;
  listAgentTokens(): Promise<AgentTokenView[]>;
  issueAgentToken(body: IssueAgentTokenRequest): Promise<AgentTokenView>;
  revokeAgentToken(tokenId: string): Promise<void>;
  listProposals(): Promise<StrategyProposalView[]>;
  requestPromoteTicket(proposalId: string): Promise<PromoteTicketView>;
  confirmPromote(proposalId: string, ticketId: string): Promise<void>;
  listExperiments(): Promise<ExperimentView[]>;
}

export function createAiClient(baseUrl: string, getToken: () => string | null): AiClient {
  const client = new AiApiClient(baseUrl, getToken);
  return {
    listProviderSettings: () => client.listProviderSettings(),
    updateProviderSetting: (provider, body) => client.updateProviderSetting(provider, body),
    listAgentTokens: () => client.listAgentTokens(),
    issueAgentToken: (body) => client.issueAgentToken(body),
    revokeAgentToken: (tokenId) => client.revokeAgentToken(tokenId),
    listProposals: () => client.listProposals(),
    requestPromoteTicket: (proposalId) => client.requestPromoteTicket(proposalId),
    confirmPromote: (proposalId, ticketId) => client.confirmPromote(proposalId, ticketId),
    listExperiments: () => client.listExperiments(),
  };
}
