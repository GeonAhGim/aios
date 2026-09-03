"""L4_risk_and_safety_v1.0.md#2.1, §9 R-07 — gross_exposure/equity 레버리지 상한.

`RiskInputs.exposure.gross_leverage`(Σ|mv|/equity, 조립 단계에서 이미
계산됨)를 `policy.leverage.default_max`와 비교한다. §2.1 원문은
`default_max × coverage_multiplier`(참조데이터 커버리지 등급별 조정)까지
요구하지만, 그 등급을 채워 넣는 입력 경로가 `RiskInputs`에 아직 없다
(레거시 `engine.py`의 동일 한계 — "파생상품 확장 시 이 필드가 채워지면
이 비교식은 그대로 재사용된다"). 이 상태는 "미검증"이며 coverage 입력이
생기면 이 파일이 곱셈으로 확장된다. 입력 결손은 base.missing()으로
DENY(I2).
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.rules.base import missing

RULE_ID = "leverage"


def check(inputs: RiskInputs, policy: RiskPolicy) -> RuleResult:
    gross_leverage = inputs.exposure.gross_leverage
    if gross_leverage is None:
        return missing(RULE_ID, "exposure.gross_leverage", unit="x")

    limit = Decimal(str(policy.leverage.default_max))
    if gross_leverage > limit:
        return RuleResult(
            rule_id=RULE_ID,
            outcome=RiskOutcome.DENY,
            reason_code="RISK_LEVERAGE_EXCEEDED",
            observed=gross_leverage,
            limit=limit,
            unit="x",
        )
    return RuleResult(
        rule_id=RULE_ID,
        outcome=RiskOutcome.ALLOW,
        observed=gross_leverage,
        limit=limit,
        unit="x",
    )
