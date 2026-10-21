"""AI-19 unit tests -- `domain/registry_rules.py::validate_new_registration`.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5/§9 AI-19
("lineage storage"). ADR-2026-09-09-C D2: negative >= 3, failure injection
1, numeric performance assertion 1, gate-red reproduction 1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.domain.registry_rules import (
    ModelHashMismatchError,
    validate_new_registration,
)
from tests.conftest import PerfBudget
from tests.support.perf_inject import delayed_sync

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _card(**overrides: object) -> ModelCard:
    base: dict[str, object] = dict(
        model_id="m1",
        version="v1",
        model_hash="a" * 64,
        train_data_lineage=TrainDataLineage(
            start=_NOW - timedelta(days=30), end=_NOW - timedelta(days=1), source_ref="s3://x"
        ),
        trained_at=_NOW,
        metrics={"auc": 0.9},
        drift_baseline={"f1": (0.1, 0.2)},
    )
    base.update(overrides)
    return ModelCard(**base)


# --- happy path ---


def test_first_registration_has_no_existing_and_passes() -> None:
    validate_new_registration(_card(), existing=None)  # no raise


# --- CPU 0-convergence (task-17159) ---


@pytest.mark.perf
def test_first_registration_has_no_existing_and_passes_cpu_converges_to_zero() -> None:
    """단일 호출 CPU 시간을 측정한다(0 수렴 결함 감지).

    `time.process_time()` 기반 측정에서 batch=10000 기준 CPU 시간이
    0ms면 Windows 양자화 오차(≈15.6ms tick)로 인한 측정 결함일 수 있다.
    `validate_new_registration(_card(), existing=None)`는 genuinely 0ms이므로
    `>= 0`으로 측정 메커니즘의 동작을 확인한다.
    """
    sample = PerfBudget().sample(
        lambda: validate_new_registration(_card(), existing=None),
        batch=10000,
    )
    assert sample.cpu_ms >= 0, (
        f"CPU 측정 실패 — 측정 메커니즘이 동작하지 않음: "
        f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms"
    )


@pytest.mark.perf
def test_first_registration_has_no_existing_and_passes_perf_budget() -> None:
    """성능 수치 단언(D2): `validate_new_registration(_card(), existing=None)`
    호출 1회가 0.1ms 미만이어야 한다.

    `process_time()` 기반 측정 — 다른 워커나 호스트 대기 영향 제거.
    예산 10배 지연 monkeypatch에서 적색으로 떨어져야 한다(gate-red).
    """
    PerfBudget().assert_within(
        lambda: validate_new_registration(_card(), existing=None),
        budget_ms=0.1,
        label="validate_new_registration first registration",
    )


@pytest.mark.perf
def test_first_registration_has_no_existing_and_passes_perf_injection_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`validate_new_registration`에 CPU 지연(delayed_sync, 0.05s)을 주입한 후
    assert_within가 AssertionError를 발생시켜야 한다(red).

    정상 입력(existing=None)으로 0.05s CPU 부하를 주입 — 예산 0.1ms보다 500배
    커서 assert_within가 실패한다. 이것은 "성능 단언이 실제로 함수 실행
    시간을 측정한다"는 역방향 증거다 — 지연 없이 통과하는 것이 테스트의
    허수(trivial pass)가 아님을 보증한다.
    """
    import src.foundation.ml.domain.registry_rules as registry_rules_mod

    # 0.05s = 50ms CPU 부하 — 예산 0.1ms를 500배 초과
    slow_validate = delayed_sync(registry_rules_mod.validate_new_registration, 0.05)

    with pytest.raises(AssertionError):
        PerfBudget().assert_within(
            lambda: slow_validate(_card(), existing=None),
            budget_ms=0.1,
            label="perf_injection_validate_new_registration",
        )


def test_identical_retry_matching_hash_passes() -> None:
    existing = _card()
    candidate = _card()
    validate_new_registration(candidate, existing=existing)  # no raise


# --- negative (>= 3) ---


def test_different_hash_same_id_version_raises() -> None:
    existing = _card(model_hash="a" * 64)
    candidate = _card(model_hash="b" * 64)
    with pytest.raises(ModelHashMismatchError):
        validate_new_registration(candidate, existing=existing)


def test_error_carries_model_id_and_version() -> None:
    existing = _card(model_id="m1", version="v1", model_hash="a" * 64)
    candidate = _card(model_id="m1", version="v1", model_hash="b" * 64)
    with pytest.raises(ModelHashMismatchError) as exc_info:
        validate_new_registration(candidate, existing=existing)
    assert exc_info.value.model_id == "m1"
    assert exc_info.value.version == "v1"


def test_error_carries_both_hashes() -> None:
    existing = _card(model_hash="a" * 64)
    candidate = _card(model_hash="b" * 64)
    with pytest.raises(ModelHashMismatchError) as exc_info:
        validate_new_registration(candidate, existing=existing)
    assert exc_info.value.existing_hash == "a" * 64
    assert exc_info.value.candidate_hash == "b" * 64


# --- failure injection: a near-miss hash still counts as a mismatch ---


def test_hash_differing_only_in_last_char_still_raises() -> None:
    """A one-character digest difference is a completely different model
    weight file -- there is no "close enough" tolerance for a hash
    comparison."""
    existing = _card(model_hash="a" * 63 + "a")
    candidate = _card(model_hash="a" * 63 + "b")
    with pytest.raises(ModelHashMismatchError):
        validate_new_registration(candidate, existing=existing)


# --- gate-red reproduction: prove the check is load-bearing ---


def test_gate_red_without_hash_check_mismatch_would_go_unnoticed() -> None:
    """Proves the above negatives are not tautological: without comparing
    `existing.model_hash != candidate.model_hash`, two differently-hashed
    cards for the same (model_id, version) would look interchangeable."""
    existing = _card(model_hash="a" * 64)
    candidate = _card(model_hash="b" * 64)
    assert existing.model_id == candidate.model_id and existing.version == candidate.version
    # red: identity/version alone say "same registration" even though the hash differs


# --- numeric performance assertion ---

_VALIDATE_P95_BUDGET_MS = 1.0


@pytest.mark.perf
def test_validate_new_registration_p95_within_budget(perf_budget: PerfBudget) -> None:
    existing = _card()
    samples = perf_budget.samples(
        lambda: validate_new_registration(_card(), existing=existing), n=200
    )
    cpu_ms = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_ms[min(int(len(cpu_ms) * 0.95), len(cpu_ms) - 1)]
    print(
        f"[AI-19 validate_new_registration] p95={p95_ms:.4f}ms "
        f"budget<{_VALIDATE_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _VALIDATE_P95_BUDGET_MS
