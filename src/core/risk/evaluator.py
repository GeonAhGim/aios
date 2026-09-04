"""L4_risk_and_safety_v1.0.md#2.1, §4.2, §9 R-16 — 규칙 순서 고정·단락 평가·
outcome 합성·`RiskDecision` 생성.

R-04(`rules/base.py`)의 `rule_error()`, R-05~R-13 규칙 10종, R-14
`limits.check_exposure_limits`, R-15 `policy_bundle.RiskRuleBundle`을 그대로
호출·참조만 한다 — 규칙 판정 자체는 여기서 재구현하지 않는다. 이 모듈의
책임은 §4.2 순서·단락·REDUCE 합성과 `latency_us` 측정뿐이다. 순수 계층
이므로 DB·시계·난수 직접 접근을 하지 않는다(`now`는 인자로 주입되고,
`decision_id`는 `trace_id`·`gate_kind`·`inputs_hash`로부터 결정론적으로
파생한다 — 같은 입력이면 항상 같은 결정을 재생할 수 있다, R2).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from uuid import UUID, uuid5

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.limits import check_exposure_limits
from src.core.risk.policy_bundle import RiskRuleBundle
from src.core.risk.rules import (
    concentration,
    correlation,
    daily_loss,
    leverage,
    max_drawdown,
    safety_state,
    strategy_allocation,
    trade_frequency,
    var_es,
)
from src.core.risk.rules.base import rule_error

# 이 모듈이 만드는 결정 id의 고정 네임스페이스(uuid5) — 별도 의미 없는
# 상수, 난수 대신 결정론적 파생을 위해서만 쓴다.
_DECISION_ID_NAMESPACE = UUID("2f6a7f7e-27b1-4d0a-9c1a-9c9f6a9b0f01")

# concentration/exposure_limits의 DENY만 "수량을 줄이면 해소 가능"할 수
# 있다(§4.2) — 다른 규칙(예: leverage, correlation)은 이 주문 하나의 수량을
# 줄인다고 해서 반드시 해소된다고 볼 근거가 없다.
_REDUCIBLE_RULES = frozenset({"exposure_limits", "concentration"})
_REDUCIBLE_UNITS = frozenset({"pct", "notional"})

_RuleFn = Callable[[RiskInputs, RiskPolicy], RuleResult]


def _exposure_limits(inputs: RiskInputs, _policy: RiskPolicy) -> RuleResult:
    return check_exposure_limits(inputs, inputs.limits)


# §4.2 평가 순서 고정 — 테스트가 이 순서를 그대로 단언한다.
_ORDER: tuple[tuple[str, _RuleFn], ...] = (
    ("safety_state", safety_state.safety_state),
    ("exposure_limits", _exposure_limits),
    ("daily_loss", daily_loss.check),
    ("max_drawdown", max_drawdown.check),
    ("leverage", leverage.check),
    ("concentration", concentration.check),
    ("strategy_allocation", strategy_allocation.check),
    ("var_es", var_es.var_es),
    ("correlation", correlation.correlation),
    ("trade_frequency", trade_frequency.trade_frequency),
)


def _reduced_quantity(result: RuleResult, inputs: RiskInputs) -> Decimal | None:
    """수량을 줄이면 이 DENY가 해소되는지 계산한다(§4.2 REDUCE 조건).

    관측값이 주문 수량에 선형 비례한다고 가정한 보수적 근사다(정확한
    "기존 노출 대비 이번 주문분만"의 한계값은 조립 계층 없이는 분리할
    수 없다) — 한도/관측값 비율만큼 전체 수량을 줄이므로 실제 필요한
    축소량보다 항상 크거나 같은 쪽으로만 오차가 난다(fail-closed 방향).
    """
    if result.rule_id not in _REDUCIBLE_RULES or result.unit not in _REDUCIBLE_UNITS:
        return None
    if inputs.intent.reduce_only:
        return None
    if result.observed is None or result.limit is None or result.observed <= 0:
        return None
    quantity = inputs.intent.quantity
    exponent = quantity.as_tuple().exponent
    quantum = Decimal(1).scaleb(exponent) if isinstance(exponent, int) else Decimal(1)
    quantum = min(quantum, Decimal(1))
    scale = result.limit / result.observed
    reduced = (quantity * scale).quantize(quantum, rounding=ROUND_DOWN)
    return reduced if reduced > 0 else None


def evaluate(
    inputs: RiskInputs,
    bundle: RiskRuleBundle,
    *,
    gate_kind: GateKind,
    trace_id: UUID,
    now: datetime,
    ttl: float,
) -> RiskDecision:
    """R-16 공개 계약. `ttl`은 초 단위, `now`는 tz-aware UTC(호출자 주입)."""
    start_ns = time.perf_counter_ns()
    policy = RiskPolicy(**bundle.policy_snapshot)

    results: list[RuleResult] = []
    terminal_deny: RuleResult | None = None
    reduced_quantity: Decimal | None = None

    for rule_id, rule in _ORDER:
        try:
            result = rule(inputs, policy)
        except Exception:  # noqa: BLE001 — I2: 규칙 예외도 fail-closed DENY
            result = rule_error(rule_id)
        results.append(result)

        if result.outcome != RiskOutcome.DENY:
            continue

        candidate = _reduced_quantity(result, inputs)
        if candidate is None:
            terminal_deny = result
            break  # 첫 (해소 불가능한) DENY에서 단락 — 이후 규칙은 평가하지 않는다.
        if reduced_quantity is None or candidate < reduced_quantity:
            reduced_quantity = candidate

    obligations: tuple[str, ...] = ()
    if terminal_deny is not None:
        outcome = RiskOutcome.DENY
    elif any(r.outcome == RiskOutcome.PAUSE for r in results):
        outcome = RiskOutcome.PAUSE
    elif reduced_quantity is not None:
        outcome = RiskOutcome.REDUCE
        obligations = (f"REDUCE_QUANTITY_TO:{reduced_quantity}",)
    elif any(r.outcome == RiskOutcome.ESCALATE for r in results):
        outcome = RiskOutcome.ESCALATE
    else:
        outcome = RiskOutcome.ALLOW

    reason_codes = tuple(r.reason_code for r in results if r.reason_code is not None)
    inputs_hash = inputs.inputs_hash()
    decision_id = uuid5(_DECISION_ID_NAMESPACE, f"{trace_id}:{gate_kind.value}:{inputs_hash}")
    latency_us = max(1, (time.perf_counter_ns() - start_ns) // 1000)

    return RiskDecision(
        decision_id=decision_id,
        gate_kind=gate_kind,
        tenant_id=inputs.tenant_id,
        execution_ref=inputs.execution_ref,
        subject_fingerprint=inputs_hash,
        outcome=outcome,
        reason_codes=reason_codes,
        obligations=obligations,
        rule_results=tuple(results),
        rule_version=bundle.version,
        rule_hash=bundle.rule_hash,
        engine_version=bundle.engine_version,
        inputs_hash=inputs_hash,
        input_refs=(),
        evaluated_at=now,
        expires_at=now + timedelta(seconds=ttl),
        trace_id=trace_id,
        evidence_ref=None,
        latency_us=latency_us,
    )
