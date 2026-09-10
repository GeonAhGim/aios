"""L4_compliance_and_regulatory_v1.0.md#9 CM-6 — restricted_list.py unit
tests. §8 boundary table: exact match / non-match / empty list / case
sensitivity / missing required field (fail-closed).

DEEPEN 2065 (task-2858, docs/audit/DEPTH_CM.md): the original CM-6 leaf
graded D1 for missing failure injection, a numeric performance assertion, a
gate-red reproduction, and D3 adversarial/multi-instance proof (negative≥5
was already satisfied). `check()` is pure (no I/O), so those four are added
as:
  - failure injection: `params["restricted_symbols"]` of a non-iterable type
    (e.g. an `int`) makes `symbol in restricted_symbols` raise `TypeError`
    inside `check()`; wired through the real `RuleBundle`/`evaluate_bundle`
    gate (not `check()` called directly), this proves `_run_rule`'s
    fail-closed DENY (CM-A2) actually covers this rule, not just the
    evaluator's own synthetic test double.
  - numeric performance: a wall-clock ceiling on 10,000 evaluations
    (pure-function regression guard, same pattern as task-2855/2856).
  - gate-red reproduction: the same `RuleBundle`/`evaluate_bundle` pipeline
    used above, run once with a restricted symbol (decision.verdict must
    turn DENY/red) and once with a clean symbol (decision.verdict must stay
    ALLOW/green) — proving this rule is what flips the gate, not that the
    gate is wired to always deny.
  - D3 adversarial + multi-instance/replay: `RuleHit` tamper rejection
    (frozen pydantic model) and byte-identical results from independent OS
    processes given the same input.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules.restricted_list import RULE_ID, check

_NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def test_symbol_on_restricted_list_denies():
    hit = check({"restricted_symbols": ("XYZ", "ABC")}, {"symbol": "XYZ"})
    assert hit is not None
    assert hit.rule_id == RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"symbol": "XYZ"}


def test_symbol_not_on_restricted_list_allows():
    hit = check({"restricted_symbols": ("XYZ", "ABC")}, {"symbol": "BTC/USDT"})
    assert hit is None


def test_empty_restricted_list_always_allows():
    hit = check({"restricted_symbols": ()}, {"symbol": "XYZ"})
    assert hit is None


def test_missing_restricted_symbols_param_defaults_to_empty_and_allows():
    hit = check({}, {"symbol": "XYZ"})
    assert hit is None


def test_match_is_case_sensitive():
    """'xyz' != 'XYZ' — no implicit normalization (documented in module
    docstring); ticker case handling is a separate future leaf's scope."""
    hit = check({"restricted_symbols": ("XYZ",)}, {"symbol": "xyz"})
    assert hit is None


def test_missing_symbol_field_fails_closed_with_deny():
    hit = check({"restricted_symbols": ("XYZ",)}, {})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"missing_field": "snapshot.symbol"}


def test_none_symbol_field_fails_closed_with_deny():
    hit = check({"restricted_symbols": ("XYZ",)}, {"symbol": None})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


# --- Failure injection (DEEPEN) ----------------------------------------------


def _bundle_of(rule_id: str, params: dict[str, Any]) -> RuleBundle:
    return RuleBundle(
        version="test", rules=(RuleSpec(rule_id=rule_id, params=params, check=check),)
    )


def test_non_iterable_restricted_symbols_fails_closed_through_the_gate() -> None:
    """실패 주입: `params["restricted_symbols"]`가 반복 불가능한 타입(예:
    `int`)이면 `symbol in restricted_symbols`가 `TypeError`를 던진다. 이
    예외를 `check()` 자신이 삼키지 않고, 실제 게이트(`RuleBundle` +
    `evaluate_bundle`)가 fail-closed DENY로 바꾸는지 증명한다(CM-A2) — 규칙
    자신이 무너져도 게이트는 조용히 ALLOW로 새지 않는다."""
    bundle = _bundle_of(RULE_ID, {"restricted_symbols": 12345})  # not iterable
    decision = evaluate_bundle(bundle, {"symbol": "XYZ"}, now=_NOW)

    assert decision.verdict == ComplianceVerdict.DENY
    assert len(decision.rule_hits) == 1
    assert decision.rule_hits[0].rule_id == RULE_ID
    assert decision.rule_hits[0].severity == ComplianceVerdict.DENY


# --- Numeric performance (DEEPEN) -------------------------------------------


def test_check_meets_latency_budget_over_many_calls() -> None:
    """수치 성능 단언: 10,000회 반복 호출의 총 지연이 넉넉한 상한(1.0s,
    호출당 평균 100us) 안에 들어야 한다 — 이후 회귀로 이 순수 함수가
    눈에 띄게 느려지면 이 테스트가 잡는다."""
    params = {"restricted_symbols": ("XYZ", "ABC", "DEF")}
    snapshot = {"symbol": "BTC/USDT"}

    started = time.perf_counter()
    for _ in range(10_000):
        check(params, snapshot)
    elapsed_s = time.perf_counter() - started

    assert elapsed_s < 1.0, f"10,000 evaluations took {elapsed_s:.3f}s (budget 1.0s)"


# --- Gate-red reproduction (DEEPEN) ------------------------------------------


def test_restricted_symbol_turns_the_gate_red_and_clean_symbol_keeps_it_green() -> None:
    """게이트 적색 재현: `check()`를 단독 호출하는 것과 별개로, 실제 규칙
    평가 파이프라인(`RuleBundle` + `evaluate_bundle`)에 이 규칙 하나만
    실어 돌렸을 때 제한 종목이면 전체 결정이 DENY(적색)로, 정상 종목이면
    ALLOW(녹색)로 정확히 갈리는지 증명한다 — 게이트가 이 규칙의 결과에
    실제로 좌우됨을 실증하지, 항상 DENY로 고정돼 있지 않음을 보여준다."""
    bundle = _bundle_of(RULE_ID, {"restricted_symbols": ("XYZ", "ABC")})

    red = evaluate_bundle(bundle, {"symbol": "XYZ"}, now=_NOW)
    assert red.verdict == ComplianceVerdict.DENY

    green = evaluate_bundle(bundle, {"symbol": "BTC/USDT"}, now=_NOW)
    assert green.verdict == ComplianceVerdict.ALLOW


# --- D3 adversarial + multi-instance/replay proof (DEEPEN) ------------------


def test_rule_hit_rejects_post_construction_tampering() -> None:
    """D3 적대적: 컴플라이언스 판정을 좌우하는 `RuleHit`을 만든 뒤 메모리
    에서 `severity`를 DENY -> ALLOW로 바꿔치기하는 시도(다운스트림 코드의
    버그 또는 공격)는 예외 없이 조용히 성공해서는 안 된다.
    `RuleHit`은 이미 `frozen=True` pydantic 모델이므로(I-09), 그 방어선이
    실제로 걸려 있음을 실증한다."""
    hit = check({"restricted_symbols": ("XYZ",)}, {"symbol": "XYZ"})
    assert hit is not None
    with pytest.raises(ValidationError):
        hit.severity = ComplianceVerdict.ALLOW  # type: ignore[misc]


def _replay_in_subprocess(params: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, str, str]:
    """Module-level so it is picklable for `ProcessPoolExecutor` on Windows
    (spawn start method)."""
    hit = check(params, snapshot)
    assert hit is not None
    return (hit.rule_id, hit.severity.value, str(hit.evidence))


def test_replay_across_independent_processes_is_byte_identical() -> None:
    """D3 다중 인스턴스/리플레이 증명: 전역 상태가 완전히 분리된 별도 OS
    프로세스 3개가 동일한 params/snapshot을 각자 평가해도 완전히 동일한
    (rule_id, severity, evidence)를 내야 한다."""
    params = {"restricted_symbols": ("XYZ", "ABC")}
    snapshot = {"symbol": "XYZ"}

    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_replay_in_subprocess, [params] * 3, [snapshot] * 3))

    assert len(results) == 3
    assert len(set(results)) == 1
