"""L4_risk_and_safety_v1.0.md#3.2, #2.1, #9 R-14 — 노출 한도 값 객체(`RiskInputs.limits`)
+ `check_exposure_limits(inputs, limits) -> RuleResult`.

값 객체(`ExposureLimit`/`LimitScope`/`LimitMetric`)는 `inputs.py`의
`RiskInputs`와 서로 의존한다 — 순환을 피하기 위해 값 객체는 여기 두고
`inputs.py`가 이 모듈에서 import한다. `check_exposure_limits`도 타입
힌트로만 `RiskInputs`를 참조하고(`TYPE_CHECKING`), R-04 `rules/base.py`의
`missing`/`pct`는 함수 본문에서 지연 import한다 — 모듈 최상단에서
`rules.base`를 import하면 `rules.base -> inputs -> limits` 역순환이 생겨
`ImportError`가 난다(`inputs.py`가 먼저 로드되는 경로에서 재현됨).

scope 매칭(§2.1 "SYMBOL 한도가 다른 심볼에 미적용" 등 6개 scope 전부):
`RiskInputs`는 아직 계정/거래소(provider) 전용 식별자 필드가 없다(R-27
조립기가 채우기 전). TENANT/STRATEGY/SYMBOL/ASSET_CLASS는 각각
`tenant_id`/`intent.strategy_id`/`intent.symbol`/`intent.asset_class`로
정확히 매칭한다. ACCOUNT는 잠정적으로 `tenant_id`를, PROVIDER는
`execution_ref`를 subject로 쓴다 — 미검증, 전용 필드가 생기면 교체한다.

`exposure.gross_notional`/`net_notional`(§3.2)는 `safety.fence_snapshot`과
같은 컨벤션으로 `"<SCOPE>:<scope_ref>"` 키를 쓴다고 가정한다(미검증 —
조립기가 이 키 규칙을 따라야 값이 채워진다).
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel

from src.core.risk.decision import RiskOutcome, RuleResult

if TYPE_CHECKING:
    from src.core.risk.inputs import RiskInputs
    from src.core.risk.rules.base import RuleUnit


class LimitScope(str, Enum):
    TENANT = "TENANT"
    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"
    SYMBOL = "SYMBOL"
    ASSET_CLASS = "ASSET_CLASS"
    PROVIDER = "PROVIDER"


class LimitMetric(str, Enum):
    GROSS_NOTIONAL_PCT = "GROSS_NOTIONAL_PCT"
    NET_NOTIONAL_PCT = "NET_NOTIONAL_PCT"
    MAX_ORDER_NOTIONAL = "MAX_ORDER_NOTIONAL"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
    MAX_TRADES_PER_HOUR = "MAX_TRADES_PER_HOUR"
    MAX_LEVERAGE = "MAX_LEVERAGE"


class ExposureLimit(BaseModel, frozen=True):
    scope: LimitScope
    scope_ref: str
    metric: LimitMetric
    limit_value: Decimal
    hard: bool
    limit_id: UUID


RULE_ID = "exposure_limits"
_PCT_METRICS = (LimitMetric.GROSS_NOTIONAL_PCT, LimitMetric.NET_NOTIONAL_PCT)


def _subject(inputs: RiskInputs, scope: LimitScope) -> str | None:
    if scope in (LimitScope.TENANT, LimitScope.ACCOUNT):
        return str(inputs.tenant_id)
    if scope == LimitScope.STRATEGY:
        return inputs.intent.strategy_id
    if scope == LimitScope.SYMBOL:
        return inputs.intent.symbol
    if scope == LimitScope.ASSET_CLASS:
        return inputs.intent.asset_class
    return inputs.execution_ref  # PROVIDER


def _observed(inputs: RiskInputs, limit: ExposureLimit) -> tuple[Decimal | None, str, RuleUnit]:
    metric = limit.metric
    if metric in _PCT_METRICS:
        key = f"{limit.scope.value}:{limit.scope_ref}"
        if metric == LimitMetric.GROSS_NOTIONAL_PCT:
            return inputs.exposure.gross_notional.get(key), f"exposure.gross_notional[{key}]", "pct"
        return inputs.exposure.net_notional.get(key), f"exposure.net_notional[{key}]", "pct"
    if metric == LimitMetric.MAX_ORDER_NOTIONAL:
        return inputs.intent.notional, "intent.notional", "notional"
    if metric == LimitMetric.MAX_OPEN_POSITIONS:
        open_count = inputs.exposure.open_positions_count
        return Decimal(open_count), "exposure.open_positions_count", "count"
    if metric == LimitMetric.MAX_TRADES_PER_HOUR:
        trades = inputs.activity.trades_last_1h
        trades_value = Decimal(trades) if trades is not None else None
        return trades_value, "activity.trades_last_1h", "count"
    return inputs.exposure.gross_leverage, "exposure.gross_leverage", "x"  # MAX_LEVERAGE


def check_exposure_limits(inputs: RiskInputs, limits: tuple[ExposureLimit, ...]) -> RuleResult:
    from src.core.risk.rules.base import missing, pct

    best: RuleResult | None = None
    best_rank = -1
    for limit in limits:
        subject = _subject(inputs, limit.scope)
        if subject is None or subject != limit.scope_ref:
            continue  # 이 주문에 적용되지 않는 scope — I2 대상이 아니다

        observed, field, unit = _observed(inputs, limit)
        if observed is None:
            return missing(RULE_ID, field, unit=unit)  # fail-closed(I2)

        is_pct = limit.metric in _PCT_METRICS
        observed_q = pct(observed) if is_pct else observed
        limit_value = pct(limit.limit_value) if is_pct else limit.limit_value
        reason_code = f"RISK_LIMIT_BREACH:{limit.scope.value}:{limit.metric.value}"

        if observed_q > limit_value:
            outcome = RiskOutcome.DENY if limit.hard else RiskOutcome.ESCALATE
            result = RuleResult(
                rule_id=RULE_ID,
                outcome=outcome,
                reason_code=reason_code,
                observed=observed_q,
                limit=limit_value,
                unit=unit,
            )
            if outcome == RiskOutcome.DENY:
                return result  # hard 위반은 다른 어떤 결과로도 뒤집히지 않는다
            rank = 1
        else:
            result = RuleResult(
                rule_id=RULE_ID,
                outcome=RiskOutcome.ALLOW,
                observed=observed_q,
                limit=limit_value,
                unit=unit,
            )
            rank = 0

        if rank > best_rank:
            best_rank = rank
            best = result

    if best is not None:
        return best
    return RuleResult(rule_id=RULE_ID, outcome=RiskOutcome.ALLOW, unit="count")
