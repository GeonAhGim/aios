"""L4_risk_and_safety_v1.0.md#2.1, §9 R-04 — 규칙 공통 계약(`Rule` Protocol)·
fail-closed 헬퍼(`missing`, `rule_error`)·`pct` 정밀도.

R-05~R-13 규칙 9종이 이 시그니처에 직렬 의존한다(§2.1 rules/*.py). 판단
자체는 하지 않는다 — 순수(I/O·DB 금지), 상한 60줄.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal, Protocol

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs

RuleUnit = Literal["pct", "x", "count", "notional"]

_PCT_QUANTUM = Decimal("0.000001")


class Rule(Protocol):
    """R-05~R-13가 구현하는 규칙 함수의 공통 시그니처."""

    def __call__(self, inputs: RiskInputs, policy: RiskPolicy) -> RuleResult: ...


def missing(rule_id: str, field: str, *, unit: RuleUnit = "pct") -> RuleResult:
    """필수 입력이 `None`이면 승인으로 새지 않는다(I2, fail-closed) —
    `RISK_INPUT_MISSING:<field>`로 DENY하고 `missing_fields`를 채운다."""
    return RuleResult(
        rule_id=rule_id,
        outcome=RiskOutcome.DENY,
        reason_code=f"RISK_INPUT_MISSING:{field}",
        unit=unit,
        missing_fields=(field,),
    )


def rule_error(rule_id: str, *, unit: RuleUnit = "count") -> RuleResult:
    """규칙 함수가 예외를 던져도 evaluator가 이 헬퍼로 DENY를 만든다
    (I2, `RISK_RULE_ERROR:<rule>`) — 예외가 조용히 ALLOW로 새지 않는다."""
    return RuleResult(
        rule_id=rule_id,
        outcome=RiskOutcome.DENY,
        reason_code=f"RISK_RULE_ERROR:{rule_id}",
        unit=unit,
    )


def pct(value: Decimal) -> Decimal:
    """비율 값을 §3.2 정밀도(0–100, 소수 6자리)로 quantize한다."""
    return value.quantize(_PCT_QUANTUM)
