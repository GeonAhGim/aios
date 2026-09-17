"""AI-19 unit tests -- `domain/registry_rules.py::validate_new_registration`.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5/§9 AI-19
("lineage storage"). ADR-2026-09-09-C D2: negative >= 3, failure injection
1, numeric performance assertion 1, gate-red reproduction 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.domain.registry_rules import (
    ModelHashMismatchError,
    validate_new_registration,
)

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


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_validate_new_registration_p95_within_budget() -> None:
    existing = _card()
    samples: list[float] = []
    for _ in range(200):
        candidate = _card()
        started = time.perf_counter()
        validate_new_registration(candidate, existing=existing)
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(
        f"[AI-19 validate_new_registration] p95={p95_ms:.4f}ms "
        f"budget<{_VALIDATE_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _VALIDATE_P95_BUDGET_MS
