"""L4_compliance_and_regulatory_v1.0.md#9 CM-6 — concentration.py unit
tests. §8 boundary table: below limit / exactly at limit (allow, strict `>`
only) / just above limit (deny) / missing param / missing snapshot field.

DEEPEN 2065 (task-2858, docs/audit/DEPTH_CM.md): the original CM-6 leaf
graded D1 for missing failure injection, a numeric performance assertion, a
gate-red reproduction, and D3 adversarial/multi-instance proof (negative≥5
was already satisfied). `check()` is pure (no I/O), so those four are added
as:
  - D3 adversarial: `nan`/`-inf` observed percentages compare `False` to
    `> limit` in plain Python, which would otherwise silently ALLOW a
    malformed (e.g. divide-by-zero-derived) projection. `check()` now
    rejects non-finite `observed`/`limit` values fail-closed (CM-A2) — this
    is a real bypass class this module owns, unlike `restricted_list.py`'s
    string matching.
  - failure injection: a non-numeric `observed` (e.g. a `str`) makes
    `observed > limit` raise `TypeError` inside `check()`; wired through the
    real `RuleBundle`/`evaluate_bundle` gate, this proves `_run_rule`'s
    fail-closed DENY (CM-A2) actually covers this rule.
  - numeric performance: a wall-clock ceiling on 10,000 evaluations.
  - gate-red reproduction: the same `RuleBundle`/`evaluate_bundle` pipeline,
    run once over the limit (decision.verdict must turn DENY/red) and once
    under it (decision.verdict must stay ALLOW/green).
  - D3 multi-instance/replay: byte-identical results from independent OS
    processes given the same input.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules.concentration import RULE_ID, check

_PARAMS = {"max_single_instrument_pct": 25.0}
_NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def test_below_limit_allows():
    hit = check(_PARAMS, {"projected_instrument_pct": 24.9})
    assert hit is None


def test_exactly_at_limit_allows():
    """Boundary case — equal to the limit does not exceed it (strict `>`,
    matching domain/rules.py's existing POLICY_MAX_* checks)."""
    hit = check(_PARAMS, {"projected_instrument_pct": 25.0})
    assert hit is None


def test_just_above_limit_denies():
    hit = check(_PARAMS, {"projected_instrument_pct": 25.1})
    assert hit is not None
    assert hit.rule_id == RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"observed_pct": 25.1, "limit_pct": 25.0}


def test_zero_limit_denies_any_positive_exposure():
    hit = check({"max_single_instrument_pct": 0.0}, {"projected_instrument_pct": 0.1})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_missing_limit_param_fails_closed_with_deny():
    hit = check({}, {"projected_instrument_pct": 10.0})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"missing_field": "params.max_single_instrument_pct"}


def test_missing_snapshot_field_fails_closed_with_deny():
    hit = check(_PARAMS, {})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"missing_field": "snapshot.projected_instrument_pct"}


# --- D3 adversarial: non-finite bypass class (DEEPEN) ------------------------


def test_nan_observed_fails_closed_instead_of_silently_allowing() -> None:
    """D3 적대적: `float('nan') > 25.0`은 파이썬에서 `False`이므로, 별도
    방어가 없다면 상류(예: 포트폴리오 총가치 0으로 나눈 계산)에서 온
    NaN 투영치가 조용히 ALLOW로 새어 나간다. 이 우회 경로를 fail-closed로
    막는지 실증한다(CM-A2)."""
    hit = check(_PARAMS, {"projected_instrument_pct": float("nan")})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_negative_infinity_observed_fails_closed_instead_of_silently_allowing() -> None:
    """D3 적대적: `float('-inf') > 25.0`도 `False` — 동일한 우회 경로."""
    hit = check(_PARAMS, {"projected_instrument_pct": float("-inf")})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_infinite_limit_param_fails_closed_instead_of_allowing_unbounded_exposure() -> None:
    """D3 적대적: 한도 자체가 `inf`(설정 오류/역직렬화 손상)면 그 어떤
    관측치도 초과할 수 없어 사실상 무제한 노출을 허용하게 된다 — 한도값도
    유한해야 한다."""
    hit = check({"max_single_instrument_pct": float("inf")}, {"projected_instrument_pct": 99.9})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


# --- Failure injection (DEEPEN) ----------------------------------------------


def _bundle_of(params: dict[str, Any]) -> RuleBundle:
    return RuleBundle(
        version="test", rules=(RuleSpec(rule_id=RULE_ID, params=params, check=check),)
    )


def test_non_numeric_observed_fails_closed_through_the_gate() -> None:
    """실패 주입: `snapshot["projected_instrument_pct"]`가 숫자가 아닌
    타입(예: `str`)이면 `observed > limit`이 `TypeError`를 던진다. 이
    예외를 `check()` 자신이 삼키지 않고, 실제 게이트(`RuleBundle` +
    `evaluate_bundle`)가 fail-closed DENY로 바꾸는지 증명한다(CM-A2)."""
    bundle = _bundle_of(_PARAMS)
    decision = evaluate_bundle(bundle, {"projected_instrument_pct": "high"}, now=_NOW)

    assert decision.verdict == ComplianceVerdict.DENY
    assert len(decision.rule_hits) == 1
    assert decision.rule_hits[0].rule_id == RULE_ID
    assert decision.rule_hits[0].severity == ComplianceVerdict.DENY


# --- Numeric performance (DEEPEN) -------------------------------------------


def test_check_meets_latency_budget_over_many_calls() -> None:
    """수치 성능 단언: 10,000회 반복 호출의 총 지연이 넉넉한 상한(1.0s,
    호출당 평균 100us) 안에 들어야 한다."""
    snapshot = {"projected_instrument_pct": 24.9}

    started = time.perf_counter()
    for _ in range(10_000):
        check(_PARAMS, snapshot)
    elapsed_s = time.perf_counter() - started

    assert elapsed_s < 1.0, f"10,000 evaluations took {elapsed_s:.3f}s (budget 1.0s)"


# --- Gate-red reproduction (DEEPEN) ------------------------------------------


def test_over_limit_turns_the_gate_red_and_under_limit_keeps_it_green() -> None:
    """게이트 적색 재현: `check()`를 단독 호출하는 것과 별개로, 실제 규칙
    평가 파이프라인(`RuleBundle` + `evaluate_bundle`)에 이 규칙 하나만
    실어 돌렸을 때 한도 초과면 전체 결정이 DENY(적색)로, 한도 이내면
    ALLOW(녹색)로 정확히 갈리는지 증명한다."""
    bundle = _bundle_of(_PARAMS)

    red = evaluate_bundle(bundle, {"projected_instrument_pct": 25.1}, now=_NOW)
    assert red.verdict == ComplianceVerdict.DENY

    green = evaluate_bundle(bundle, {"projected_instrument_pct": 24.9}, now=_NOW)
    assert green.verdict == ComplianceVerdict.ALLOW


# --- D3 multi-instance/replay proof (DEEPEN) --------------------------------


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
    snapshot = {"projected_instrument_pct": 25.1}

    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_replay_in_subprocess, [_PARAMS] * 3, [snapshot] * 3))

    assert len(results) == 3
    assert len(set(results)) == 1
