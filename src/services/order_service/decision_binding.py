"""WORM `risk_decision` 행 ↔ 제출 주문 결속 검증 — 순수 규칙(I/O 없음).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.6 2~3단계, §4.1 I1(fingerprint
일치)·I4(관측 F0와 현재가 다르면 부작용 금지)·I10(ALLOW는 한 subject 전용,
다른 intent에 이전 불가). task-1520(51be3c7)·task-1532 재현 두 건을 막는다:

- I10: 같은 tenant의 ALLOW `decision_id`를 다른 execution·심볼·수량 주문에
  붙이면 트리거(tenant·outcome·만료만 검사)도 `fenced_submit`도 통과했다.
- I4: 호출자가 `GateDecision.fence_snapshot`(F0)을 부풀리면 kill switch 뒤
  F1이 F0보다 크지 않아 stale 판정이 나지 않았다.

권위는 WORM 행이다: `RiskDecision.execution_ref`와 `inputs_snapshot`의
`fence_snapshot`(R-35 `_PreSubmitInputs`가 기록)·intent(`symbol`·`side`·
`quantity`)를 주문·호출자 값과 대조한다. 호출자 F0는 WORM F0와 **같아야**
하며 다르면 거부한다(무시하고 덮어쓰지 않는다 — 다르다는 사실 자체가
위조 증거라 감사에 남겨야 한다). 결손(intent·fence 미기록, execution_ref
None)은 전부 불일치로 본다 — fail-closed(I-01).

`fenced_submit.py`와 같은 이유로 foundation을 import하지 않는다. 에러
taxonomy는 §3.4 `INTEGRITY_RISK_FINGERPRINT_MISMATCH`를 재사용한다(신설
금지, task-1532 decision).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from src.core.risk.decision import RiskDecision
from src.data.models.trading import Order

REASON_INTEGRITY_MISMATCH = "INTEGRITY_RISK_FINGERPRINT_MISMATCH"
MISMATCH_DECISION_MISSING = "decision_missing"

_INTENT_FIELDS = ("symbol", "side", "quantity")


@dataclass(frozen=True)
class DecisionBinding:
    """검증 결과. `mismatches`가 비어 있을 때만 `fence_snapshot`(WORM에서 읽은
    F0)을 신뢰해 쓴다 — 불일치가 하나라도 있으면 F0는 비어 있다."""

    mismatches: tuple[str, ...]
    fence_snapshot: Mapping[str, int]

    @property
    def ok(self) -> bool:
        return not self.mismatches


def execution_ref_for(order: Order) -> str:
    """`fence_pairs_for`·`foundation_gate`와 같은 `exec:<execution_id>` 형식."""
    return f"exec:{order.execution_id}"


def _fence_from_snapshot(inputs_snapshot: Mapping[str, Any]) -> dict[str, int] | None:
    raw = inputs_snapshot.get("fence_snapshot")
    if not isinstance(raw, Mapping) or not raw:
        return None
    fence: dict[str, int] = {}
    for pair, token in raw.items():
        if not isinstance(pair, str) or isinstance(token, bool) or not isinstance(token, int):
            return None
        fence[pair] = token
    return fence


def _intent_mismatches(order: Order, inputs_snapshot: Mapping[str, Any]) -> list[str]:
    """symbol·side·quantity 셋 다 기록돼 있고 주문과 같아야 한다."""
    mismatches: list[str] = []
    if inputs_snapshot.get("symbol") != order.symbol:
        mismatches.append("symbol")
    if inputs_snapshot.get("side") != order.side.value:
        mismatches.append("side")
    recorded_quantity = inputs_snapshot.get("quantity")
    try:
        quantity_ok = recorded_quantity is not None and Decimal(
            str(recorded_quantity)
        ) == order.quantity
    except InvalidOperation:
        quantity_ok = False
    if not quantity_ok:
        mismatches.append("quantity")
    return mismatches


def verify_decision_binding(
    order: Order,
    *,
    caller_fence: Mapping[str, int],
    recorded: RiskDecision,
    inputs_snapshot: Mapping[str, Any],
) -> DecisionBinding:
    """WORM 행(`recorded`, `inputs_snapshot`)이 이 주문·이 호출자 F0에 결속돼
    있는지 검사한다. tenant·outcome·만료는 여기서 다시 보지 않는다 — 조회가
    tenant 스코프이고(I8) 나머지는 `orders` 트리거(`93c0e7f6b8d9`)가 거부한다.
    반환 `mismatches`는 결정론적 순서의 필드 이름만 담는다(값은 감사에 남기지
    않는다, §7)."""
    mismatches: list[str] = []
    if recorded.execution_ref is None or recorded.execution_ref != execution_ref_for(order):
        mismatches.append("execution_ref")
    mismatches.extend(_intent_mismatches(order, inputs_snapshot))

    worm_fence = _fence_from_snapshot(inputs_snapshot)
    if worm_fence is None:
        mismatches.append("fence_snapshot_missing")
    elif dict(caller_fence) != worm_fence:
        mismatches.append("fence_snapshot")

    if mismatches or worm_fence is None:  # worm_fence None이면 위에서 이미 불일치다
        return DecisionBinding(tuple(mismatches), {})
    return DecisionBinding((), worm_fence)


__all__ = [
    "DecisionBinding",
    "MISMATCH_DECISION_MISSING",
    "REASON_INTEGRITY_MISMATCH",
    "execution_ref_for",
    "verify_decision_binding",
]
