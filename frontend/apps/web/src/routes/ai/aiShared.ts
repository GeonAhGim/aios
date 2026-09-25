import type { AgentScope, AiProviderKind, StrategyProposalView } from "@aios/shared-types";

// task-2657(AI-22): AiStudioPage.tsx가 4개 섹션 파일(AiProviderSettings/AiTokens/
// AiProposals/AiExperiments)로 나뉘면서(ADR-2026-09-10-C 500줄 경고) 공유하는
// 상수·타입. 키 문자열은 catalog.ko.ts(ai.*)와 1:1 대응한다.
export type ProviderNameKey =
  | "ai.providers.name.anthropic"
  | "ai.providers.name.openaiCompatible"
  | "ai.providers.name.gemini"
  | "ai.providers.name.externalAgent";

export const PROVIDER_NAME_KEY: Record<AiProviderKind, ProviderNameKey> = {
  anthropic: "ai.providers.name.anthropic",
  openai_compatible: "ai.providers.name.openaiCompatible",
  gemini: "ai.providers.name.gemini",
  external_agent: "ai.providers.name.externalAgent",
};

export type ScopeLabelKey =
  | "ai.tokens.scope.read"
  | "ai.tokens.scope.research"
  | "ai.tokens.scope.propose"
  | "ai.tokens.scope.paper";

export const SCOPE_LABEL_KEY: Record<AgentScope, ScopeLabelKey> = {
  read: "ai.tokens.scope.read",
  research: "ai.tokens.scope.research",
  propose: "ai.tokens.scope.propose",
  paper: "ai.tokens.scope.paper",
};

export const AGENT_SCOPES: AgentScope[] = ["read", "research", "propose", "paper"];

export const OUTCOME_TONE: Record<StrategyProposalView["outcome"], "neutral" | "success" | "danger"> = {
  PENDING: "neutral",
  PASS: "success",
  FAIL: "danger",
};
