// spec L4_ai_research_strategy_factory_v1.0.md §2.1(gateway)/§2.2(providers)/
// §2.3(factory)/§2.4(experiments) 계약 요지 — AI-22(AiStudioPage.tsx: 공급자
// 설정·토큰·제안 목록·실험 비교·승격 버튼 확인 흐름)가 그리는 화면 모델이다.
// 그 모듈들과 이를 감싸는 API 라우터(src/api/routers/ai.py, AI-17)는 아직 없다
// (apiRoutes.ts의 ai.* implemented=false 참조, follow.ts/AI-22와 동일 사유의
// 선행 프론트) — 라우터가 생기면 실제 응답 스키마와 대조해 고친다.

/** spec §3 "스코프 의미" — LIVE·자금·정책·토큰 관리 스코프는 존재하지 않는다. */
export type AgentScope = "read" | "research" | "propose" | "paper";

export type AiProviderKind = "anthropic" | "openai_compatible" | "gemini" | "external_agent";

export interface AiProviderSettingView {
  provider: AiProviderKind;
  enabled: boolean;
  /** Decimal 문자열(marketplace.ts ListingResponse.price와 동일 관용) — 로컬 반올림 금지. */
  dailyBudgetUsd: string;
}

export interface UpdateAiProviderSettingRequest {
  enabled: boolean;
  dailyBudgetUsd: string;
}

export interface AgentTokenView {
  tokenId: string;
  scopes: AgentScope[];
  allowInstruments: string[];
  notionalCap: string;
  /** 계약상 항상 true(spec §1 "권한 분리" 행) — LIVE 실행 스코프는 존재하지 않는다. */
  paperOnly: true;
  expiresAt: string;
  revoked: boolean;
  createdAt: string;
}

export interface IssueAgentTokenRequest {
  scopes: AgentScope[];
  allowInstruments: string[];
  notionalCap: string;
  expiresAt: string;
}

export type StrategyProposalOutcome = "PENDING" | "PASS" | "FAIL";

export interface StrategyProposalView {
  proposalId: string;
  hypothesis: string;
  dataScopeInstruments: string[];
  providerRef: string;
  createdByToken: string;
  outcome: StrategyProposalOutcome;
  createdAt: string;
}

export type ExperimentKind = "backtest" | "sweep" | "walk_forward" | "paper";

export interface ExperimentView {
  experimentId: string;
  kind: ExperimentKind;
  reproducibilityKey: string;
  metrics: Record<string, string>;
  parentId: string | null;
  createdBy: string;
  createdAt: string;
}

/** AI-4 domain/confirm.py ConfirmTicket{ticket_id, action_digest, expires_at} —
 * 승격 버튼의 확인 흐름(미리보기 digest = 실행 digest, 단일 사용, TTL)을 그대로
 * 화면에 옮긴다. 프론트는 digest를 재계산하지 않고 서버 값을 그대로 보여준다. */
export interface PromoteTicketView {
  ticketId: string;
  actionDigest: string;
  expiresAt: string;
}
