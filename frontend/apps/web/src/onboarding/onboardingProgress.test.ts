import { describe, expect, it } from "vitest";
import { perfBudgetMs } from "../test/perfBudget";
import {
  computeOnboardingProgress,
  deriveOnboardingProgress,
  ONBOARDING_STEP_ORDER,
  type OnboardingCompletionFlags,
  type OnboardingProgress,
} from "./onboardingProgress";

const NONE: OnboardingCompletionFlags = {
  hasExchangeConnection: false,
  hasStrategy: false,
  hasPaperDeployment: false,
};

describe("computeOnboardingProgress happy path", () => {
  it("아무 단계도 완료되지 않았으면 첫 단계(거래소 연결)가 현재 단계다", () => {
    const progress = computeOnboardingProgress(NONE);
    expect(progress.currentStepId).toBe("connectExchange");
    expect(progress.completedCount).toBe(0);
    expect(progress.isComplete).toBe(false);
  });

  it("거래소 연결까지 끝나면 전략 생성이 현재 단계다", () => {
    const progress = computeOnboardingProgress({ ...NONE, hasExchangeConnection: true });
    expect(progress.currentStepId).toBe("createStrategy");
    expect(progress.completedCount).toBe(1);
  });

  it("거래소+전략까지 끝나면 페이퍼 실행이 현재 단계다", () => {
    const progress = computeOnboardingProgress({
      ...NONE,
      hasExchangeConnection: true,
      hasStrategy: true,
    });
    expect(progress.currentStepId).toBe("runPaper");
    expect(progress.completedCount).toBe(2);
  });

  it("세 단계 모두 완료되면 currentStepId=null, isComplete=true(첫 실행 완주)", () => {
    const progress = computeOnboardingProgress({
      hasExchangeConnection: true,
      hasStrategy: true,
      hasPaperDeployment: true,
    });
    expect(progress.currentStepId).toBeNull();
    expect(progress.completedCount).toBe(3);
    expect(progress.isComplete).toBe(true);
  });
});

describe("computeOnboardingProgress negative", () => {
  it("negative: 앞 단계(거래소 연결)가 미완료인데 뒤 단계(전략) 플래그만 true여도 현재 단계는 여전히 거래소 연결이다", () => {
    const progress = computeOnboardingProgress({
      hasExchangeConnection: false,
      hasStrategy: true,
      hasPaperDeployment: false,
    });
    expect(progress.currentStepId).toBe("connectExchange");
    expect(progress.completedCount).toBe(1);
    expect(progress.isComplete).toBe(false);
  });

  it("negative: 중간 단계(전략)만 미완료고 앞뒤가 true여도 현재 단계는 전략 생성이다(순서 불변식)", () => {
    const progress = computeOnboardingProgress({
      hasExchangeConnection: true,
      hasStrategy: false,
      hasPaperDeployment: true,
    });
    expect(progress.currentStepId).toBe("createStrategy");
  });

  it("negative: deriveOnboardingProgress는 배열이 아닌 data(undefined)를 받아도 예외 없이 미완료로 처리한다", () => {
    expect(() =>
      deriveOnboardingProgress({
        exchangeCredentials: { data: undefined, isError: false },
        strategies: { data: undefined, isError: false },
        paperDeployments: { data: undefined, isError: false },
      }),
    ).not.toThrow();

    const progress = deriveOnboardingProgress({
      exchangeCredentials: { data: undefined, isError: false },
      strategies: { data: undefined, isError: false },
      paperDeployments: { data: undefined, isError: false },
    });
    expect(progress.currentStepId).toBe("connectExchange");
  });

  it("negative: paperDeployments.data가 deployments 필드 없는 객체(봉투 스키마 불일치)여도 예외 없이 미완료로 처리한다", () => {
    const progress = deriveOnboardingProgress({
      exchangeCredentials: { data: [{ id: 1 }], isError: false },
      strategies: { data: [{ id: "s1" }], isError: false },
      paperDeployments: { data: { unexpectedShape: true }, isError: false },
    });
    expect(progress.currentStepId).toBe("runPaper");
    expect(progress.isComplete).toBe(false);
  });
});

describe("deriveOnboardingProgress 실패 주입", () => {
  it("실패 주입: 거래소 크리덴셜 조회가 isError=true면 캐시된 비어있지 않은 data가 남아 있어도 완료로 인정하지 않는다(fail-closed)", () => {
    const progress = deriveOnboardingProgress({
      // react-query는 조회가 실패해도 이전 성공 응답을 data에 남겨둘 수 있다 —
      // isError만 보고 fail-closed해야 하며 stale data.length > 0에 속으면 안 된다.
      exchangeCredentials: { data: [{ id: 1, exchange: "bitget" }], isError: true },
      strategies: { data: [], isError: false },
      paperDeployments: { data: { deployments: [] }, isError: false },
    });
    expect(progress.currentStepId).toBe("connectExchange");
    expect(progress.completedCount).toBe(0);
  });
});

describe("deriveOnboardingProgress 성능 예산", () => {
  it("대량 반복 호출(n=100000)도 100ms 예산 안에서 끝난다 — 호출당 O(1) 상한을 못박는다", () => {
    const slices = {
      exchangeCredentials: { data: [{ id: 1 }], isError: false },
      strategies: { data: [{ id: "s1" }], isError: false },
      paperDeployments: { data: { deployments: [{ id: "d1" }] }, isError: false },
    };

    const start = performance.now();
    for (let i = 0; i < 100_000; i += 1) {
      deriveOnboardingProgress(slices);
    }
    const elapsedMs = performance.now() - start;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(100));
  });
});

// 게이트 적색 재현: "완료 개수로 다음 단계를 추정"하는 회귀 구현은 실제 구현과
// 달리 순서 불변식을 지키지 않는다 — 앞 단계가 미완료인데 뒷 단계 플래그가
// 먼저 true가 되는 입력(정합성 붕괴·시딩 데이터 등)에서 서로 다른 currentStepId를
// 내놓아 이 대조 테스트가 즉시 실패로 드러낸다.
function countBasedRegressionComputeOnboardingProgress(
  flags: OnboardingCompletionFlags,
): OnboardingProgress {
  const completedCount = ONBOARDING_STEP_ORDER.filter(
    (id) =>
      flags[
        ({ connectExchange: "hasExchangeConnection", createStrategy: "hasStrategy", runPaper: "hasPaperDeployment" } as const)[
          id
        ]
      ],
  ).length;
  const currentStepId =
    completedCount < ONBOARDING_STEP_ORDER.length ? ONBOARDING_STEP_ORDER[completedCount] : null;
  return {
    steps: ONBOARDING_STEP_ORDER.map((id, i) => ({ id, complete: i < completedCount })),
    currentStepId,
    completedCount,
    isComplete: currentStepId === null,
  };
}

describe("게이트 적색 재현", () => {
  it("실제 구현은 순서 불변식을 지키지만, 완료 개수 기반 회귀 구현은 앞 단계 미완료를 놓친다", () => {
    const flags: OnboardingCompletionFlags = {
      hasExchangeConnection: false,
      hasStrategy: true,
      hasPaperDeployment: false,
    };

    const real = computeOnboardingProgress(flags);
    const regression = countBasedRegressionComputeOnboardingProgress(flags);

    expect(real.currentStepId).toBe("connectExchange");
    expect(regression.currentStepId).not.toBe("connectExchange");
  });
});
