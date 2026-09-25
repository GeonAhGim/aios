// spec L4_product_experience_and_discovery_v1.0.md §2.3 `src/foundation/whatif/`
// 1:1 대응 — `application/preview_order.py`(UX-10, 읽기 전용 가상 주문 영향 산출),
// `domain/impact.py`(UX-9, 노출·집중도·VaR·한도 여유 델타), `application/
// rebalance_plan.py`(UX-11, 목표 비중 → 이탈 → 주문 초안)는 아직 없다(task-2696
// UX-12는 프론트만 선행, apiRoutes.ts의 whatif.previewOrder·whatif.rebalancePlan
// implemented=false 참조). rebalance_plan.py가 감싸는 core/portfolio/rebalance.py
// (TradeLeg/RebalancePlan)는 이미 있어 그 필드 형태를 그대로 따른다 — 라우터가
// 생기면 실제 응답 스키마와 대조해 고친다.

export type WhatIfOrderSide = "BUY" | "SELL";

export interface WhatIfOrderInput {
  symbol: string;
  side: WhatIfOrderSide;
  /** Decimal 문자열(marketplace.ts ListingResponse.price와 동일 관용) — 로컬 반올림 금지. */
  quantity: string;
}

// domain/impact.py(UX-9)가 명시하는 4개 델타 — 서버가 계산한 값을 그대로 표시하고
// 프론트에서 재계산하지 않는다(screener.ts truncated와 동일한 A5류 불변조건).
export interface WhatIfImpactResponse {
  exposureDeltaPct: string;
  concentrationDeltaPct: string;
  varDelta: string;
  limitHeadroomDelta: string;
}

export interface WhatIfRebalanceTargetInput {
  symbol: string;
  /** 목표 비중(%), Decimal 문자열. */
  targetWeightPct: string;
}

// core/portfolio/rebalance.py의 TradeLeg/RebalancePlan(BaseModel)과 1:1 대응.
export interface WhatIfTradeLegView {
  symbol: string;
  side: WhatIfOrderSide;
  quantity: string;
  price: string;
  notional: string;
  deltaWeightPct: string;
}

export interface WhatIfRebalancePlanResponse {
  trades: WhatIfTradeLegView[];
  turnoverPct: string;
  estCost: string;
  /** "{symbol}:REBALANCE_BAND" | "{symbol}:MIN_TRADE_NOTIONAL" 형식의 사유 코드. */
  skipped: string[];
}

/** describeWhatIfOrderIssues가 돌려주는 코드 — WhatIfPanel.tsx가 이 코드를 catalog.ko의
 * whatif.orderValidation.* 키로 매핑해 보여준다(screener.ts ScreenerValidationIssue와
 * 동일 관용 — shared-types는 apps/web의 i18n 카탈로그에 의존할 수 없다). */
export type WhatIfOrderValidationIssue =
  | { code: "symbol_required" }
  | { code: "quantity_invalid" };

const NON_NEGATIVE_DECIMAL = /^\d+(\.\d+)?$/;

function isPositiveDecimal(value: string): boolean {
  return NON_NEGATIVE_DECIMAL.test(value.trim()) && Number(value) > 0;
}

// 가상 주문이 preview_order.py(UX-10, 읽기 전용)로 나가기 전에 막아야 하는 최소
// 계약 위반 — 수량 0 이하·비수치는 서버가 거부하기 전에 프론트에서 먼저 막는다.
export function describeWhatIfOrderIssues(order: WhatIfOrderInput): WhatIfOrderValidationIssue[] {
  const issues: WhatIfOrderValidationIssue[] = [];
  if (order.symbol.trim() === "") {
    issues.push({ code: "symbol_required" });
  }
  if (!isPositiveDecimal(order.quantity)) {
    issues.push({ code: "quantity_invalid" });
  }
  return issues;
}

/** describeRebalanceTargetIssues가 돌려주는 코드 — RebalancePage.tsx가 whatif.targetValidation.*
 * 키로 매핑한다. */
export type WhatIfRebalanceTargetValidationIssue =
  | { code: "targets_required" }
  | { code: "target_symbol_required"; index: number }
  | { code: "target_weight_invalid"; index: number }
  | { code: "target_symbol_duplicate"; index: number };

function isValidWeightPct(value: string): boolean {
  if (!NON_NEGATIVE_DECIMAL.test(value.trim())) return false;
  const n = Number(value);
  return n >= 0 && n <= 100;
}

// 목표 비중 표가 rebalance_plan.py(UX-11)로 나가기 전에 막아야 하는 최소 계약
// 위반 — 빈 표·빈 심볼·범위 밖 비중(0~100%)·중복 심볼(plan_rebalance가 symbol을
// dict key로 접어 마지막 값만 남기는 조용한 덮어쓰기를 막는다).
export function describeRebalanceTargetIssues(
  targets: WhatIfRebalanceTargetInput[],
): WhatIfRebalanceTargetValidationIssue[] {
  const issues: WhatIfRebalanceTargetValidationIssue[] = [];
  if (targets.length === 0) {
    issues.push({ code: "targets_required" });
    return issues;
  }
  const seenSymbols = new Set<string>();
  targets.forEach((target, index) => {
    if (target.symbol.trim() === "") {
      issues.push({ code: "target_symbol_required", index });
      return;
    }
    if (seenSymbols.has(target.symbol)) {
      issues.push({ code: "target_symbol_duplicate", index });
    }
    seenSymbols.add(target.symbol);
    if (!isValidWeightPct(target.targetWeightPct)) {
      issues.push({ code: "target_weight_invalid", index });
    }
  });
  return issues;
}
