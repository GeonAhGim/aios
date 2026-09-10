"""L4_compliance_and_regulatory_v1.0.md#9 CM-7 — `domain/rules/position_limit.py`
unit tests.

task-2066 DoD mapping: (1) three exact-Decimal boundary points, (4) missing
input is fail-closed DENY.

DEEPEN 2066 (task-2859, docs/audit/DEPTH_CM.md): the original CM-7 leaf
graded D1 for missing failure injection, a numeric performance assertion, a
gate-red reproduction, and D3 adversarial/multi-instance proof (negative≥5
was already satisfied). `check()` is pure (no I/O), so those four are added
as:
  - failure injection: a `Decimal` subclass whose `__gt__` raises makes
    `projected > max_position` raise inside `check()`; wired through the
    real `RuleBundle`/`evaluate_bundle` gate (not `check()` called
    directly), this proves `_run_rule`'s fail-closed DENY (CM-A2) actually
    covers this rule — a corrupted numeric type that still passes the
    `isinstance(value, Decimal)` guard must never silently ALLOW.
  - numeric performance: a wall-clock ceiling on 10,000 evaluations.
  - gate-red reproduction: the same `RuleBundle`/`evaluate_bundle` pipeline,
    run once over the limit (decision.verdict must turn DENY/red) and once
    under it (decision.verdict must stay ALLOW/green).
  - D3 adversarial + multi-instance/replay: `RuleHit` tamper rejection
    (frozen pydantic model) and byte-identical results from independent OS
    processes given the same input.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules import position_limit

_MAX_POSITION = Decimal("50000.00")
_NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


class _ExplodingDecimal(Decimal):
    """A `Decimal` subclass that still passes `isinstance(value, Decimal)`
    but raises on comparison — simulates a corrupted numeric type slipping
    past `position_limit._decimal_or_none`'s type guard."""

    def __gt__(self, other: object) -> bool:
        raise ArithmeticError("simulated corrupted Decimal comparison")


def test_position_limit_exactly_at_limit_passes():
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("50000.00")}
    assert position_limit.check(params, snapshot) is None


def test_position_limit_one_tick_over_limit_denies():
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("50000.01")}
    hit = position_limit.check(params, snapshot)
    assert hit is not None
    assert hit.rule_id == position_limit.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["projected_position_notional"] == "50000.01"
    assert hit.evidence["max_position_notional"] == "50000.00"


def test_position_limit_below_limit_passes():
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("49999.99")}
    assert position_limit.check(params, snapshot) is None


def test_position_limit_missing_param_is_fail_closed_deny():
    hit = position_limit.check({}, {"projected_position_notional": Decimal("1.00")})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "max_position_notional"


def test_position_limit_missing_snapshot_field_is_fail_closed_deny():
    hit = position_limit.check({"max_position_notional": _MAX_POSITION}, {})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "projected_position_notional"


def test_position_limit_non_decimal_input_is_fail_closed_deny():
    hit = position_limit.check(
        {"max_position_notional": 50000.0},
        {"projected_position_notional": Decimal("1.0")},
    )
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_position_limit_check_is_pure_and_deterministic():
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("50000.01")}
    assert position_limit.check(params, snapshot) == position_limit.check(params, snapshot)


# --- Failure injection (DEEPEN) ----------------------------------------------


def _bundle_of(params: dict[str, Any]) -> RuleBundle:
    return RuleBundle(
        version="test",
        rules=(
            RuleSpec(rule_id=position_limit.RULE_ID, params=params, check=position_limit.check),
        ),
    )


def test_corrupted_decimal_comparison_fails_closed_through_the_gate() -> None:
    """실패 주입: `snapshot["projected_position_notional"]`이 `isinstance(...,
    Decimal)`은 통과하지만 비교 연산에서 예외를 던지는 손상된 서브클래스면
    `check()` 자신이 아니라 실제 게이트(`RuleBundle` + `evaluate_bundle`)가
    fail-closed DENY로 바꾸는지 증명한다(CM-A2)."""
    bundle = _bundle_of({"max_position_notional": _MAX_POSITION})
    snapshot = {"projected_position_notional": _ExplodingDecimal("50000.01")}
    decision = evaluate_bundle(bundle, snapshot, now=_NOW)

    assert decision.verdict == ComplianceVerdict.DENY
    assert len(decision.rule_hits) == 1
    assert decision.rule_hits[0].rule_id == position_limit.RULE_ID
    assert decision.rule_hits[0].severity == ComplianceVerdict.DENY


# --- Numeric performance (DEEPEN) -------------------------------------------


def test_position_limit_check_meets_latency_budget_over_many_calls() -> None:
    """수치 성능 단언: 10,000회 반복 호출의 총 지연이 넉넉한 상한(1.0s,
    호출당 평균 100us) 안에 들어야 한다."""
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("49999.99")}

    started = time.perf_counter()
    for _ in range(10_000):
        position_limit.check(params, snapshot)
    elapsed_s = time.perf_counter() - started

    assert elapsed_s < 1.0, f"10,000 evaluations took {elapsed_s:.3f}s (budget 1.0s)"


# --- Gate-red reproduction (DEEPEN) ------------------------------------------


def test_position_limit_over_limit_turns_the_gate_red_and_under_limit_keeps_it_green() -> None:
    """게이트 적색 재현: `check()`를 단독 호출하는 것과 별개로, 실제 규칙
    평가 파이프라인(`RuleBundle` + `evaluate_bundle`)에 이 규칙 하나만
    실어 돌렸을 때 한도 초과면 전체 결정이 DENY(적색)로, 한도 이내면
    ALLOW(녹색)로 정확히 갈리는지 증명한다."""
    bundle = _bundle_of({"max_position_notional": _MAX_POSITION})

    red = evaluate_bundle(bundle, {"projected_position_notional": Decimal("50000.01")}, now=_NOW)
    assert red.verdict == ComplianceVerdict.DENY

    green = evaluate_bundle(bundle, {"projected_position_notional": Decimal("49999.99")}, now=_NOW)
    assert green.verdict == ComplianceVerdict.ALLOW


# --- D3 adversarial + multi-instance/replay proof (DEEPEN) ------------------


def test_position_limit_rule_hit_rejects_post_construction_tampering() -> None:
    """D3 적대적: 컴플라이언스 판정을 좌우하는 `RuleHit`을 만든 뒤 메모리
    에서 `severity`를 DENY -> ALLOW로 바꿔치기하는 시도(다운스트림 코드의
    버그 또는 공격)는 예외 없이 조용히 성공해서는 안 된다. `RuleHit`은 이미
    `frozen=True` pydantic 모델이므로(I-09), 그 방어선이 실제로 걸려
    있음을 실증한다."""
    hit = position_limit.check(
        {"max_position_notional": _MAX_POSITION},
        {"projected_position_notional": Decimal("50000.01")},
    )
    assert hit is not None
    with pytest.raises(ValidationError):
        hit.severity = ComplianceVerdict.ALLOW  # type: ignore[misc]


def _replay_in_subprocess(params: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, str, str]:
    """Module-level so it is picklable for `ProcessPoolExecutor` on Windows
    (spawn start method)."""
    hit = position_limit.check(params, snapshot)
    assert hit is not None
    return (hit.rule_id, hit.severity.value, str(hit.evidence))


def test_position_limit_replay_across_independent_processes_is_byte_identical() -> None:
    """D3 다중 인스턴스/리플레이 증명: 전역 상태가 완전히 분리된 별도 OS
    프로세스 3개가 동일한 params/snapshot을 각자 평가해도 완전히 동일한
    (rule_id, severity, evidence)를 내야 한다."""
    params = {"max_position_notional": _MAX_POSITION}
    snapshot = {"projected_position_notional": Decimal("50000.01")}

    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_replay_in_subprocess, [params] * 3, [snapshot] * 3))

    assert len(results) == 3
    assert len(set(results)) == 1
