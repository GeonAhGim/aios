// UX-20: 온보딩 흐름(거래소 연결 → 전략 → 페이퍼 실행)의 "지금 어느 단계인가"를
// 계산하는 순수 로직. 화면(OnboardingFlowPage)과 분리해두면 훅 모킹 없이 단위
// 테스트로 negative/failure-injection/perf 증빙을 쌓을 수 있다.

export type OnboardingStepId = "connectExchange" | "createStrategy" | "runPaper";

export const ONBOARDING_STEP_ORDER: readonly OnboardingStepId[] = [
  "connectExchange",
  "createStrategy",
  "runPaper",
];

export interface OnboardingStepStatus {
  id: OnboardingStepId;
  complete: boolean;
}

export interface OnboardingProgress {
  steps: OnboardingStepStatus[];
  currentStepId: OnboardingStepId | null;
  completedCount: number;
  isComplete: boolean;
}

export interface OnboardingCompletionFlags {
  hasExchangeConnection: boolean;
  hasStrategy: boolean;
  hasPaperDeployment: boolean;
}

const FLAG_BY_STEP: Record<OnboardingStepId, keyof OnboardingCompletionFlags> = {
  connectExchange: "hasExchangeConnection",
  createStrategy: "hasStrategy",
  runPaper: "hasPaperDeployment",
};

// 순서상 앞 단계가 미완료면 뒤 단계 플래그가 true여도(서버 데이터 시딩·경합 등으로
// 정합성이 깨진 경우 포함) "현재 단계"로 인정하지 않는다 — 그렇지 않으면 사용자가
// 아직 거래소를 연결하지 않았는데도 CTA가 숨어 첫 실행을 완주하지 못한다.
export function computeOnboardingProgress(flags: OnboardingCompletionFlags): OnboardingProgress {
  const steps = ONBOARDING_STEP_ORDER.map((id) => ({ id, complete: flags[FLAG_BY_STEP[id]] }));
  const firstIncomplete = steps.find((step) => !step.complete) ?? null;
  return {
    steps,
    currentStepId: firstIncomplete?.id ?? null,
    completedCount: steps.filter((step) => step.complete).length,
    isComplete: firstIncomplete === null,
  };
}

export interface OnboardingQuerySlice {
  data: unknown;
  isError: boolean;
}

export interface OnboardingQuerySlices {
  exchangeCredentials: OnboardingQuerySlice;
  strategies: OnboardingQuerySlice;
  paperDeployments: OnboardingQuerySlice;
}

function hasNonEmptyList(slice: OnboardingQuerySlice, extract: (data: unknown) => unknown): boolean {
  // fail-closed: 조회가 실패(isError)했으면 이전에 캐시된 data가 남아 있어도
  // "완료됨"으로 오인하지 않는다 — 실패 시 안전한 쪽(미완료)으로 수렴시킨다.
  if (slice.isError) return false;
  const items = extract(slice.data);
  return Array.isArray(items) && items.length > 0;
}

/** react-query 훅 결과(data/isError)를 직접 넘겨 진행 상태를 계산한다. */
export function deriveOnboardingProgress(slices: OnboardingQuerySlices): OnboardingProgress {
  return computeOnboardingProgress({
    hasExchangeConnection: hasNonEmptyList(slices.exchangeCredentials, (d) => d),
    hasStrategy: hasNonEmptyList(slices.strategies, (d) => d),
    hasPaperDeployment: hasNonEmptyList(
      slices.paperDeployments,
      (d) => (d && typeof d === "object" && "deployments" in d ? (d as { deployments: unknown }).deployments : undefined),
    ),
  });
}
