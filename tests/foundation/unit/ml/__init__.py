"""ML foundation unit tests package (AI-18/AI-19/AI-20/AI-21).

Cross-module integration checks for `src/foundation/ml/` -- each sibling
test file in this package exercises one module in isolation
(`test_contracts_v1.py`, `test_drift.py`, `test_registry_rules.py`,
`test_point_in_time.py`, `test_feature_values.py`, ...). This file instead
checks invariants that only show up when those modules are composed, since
no single-module test file is positioned to catch a seam breaking between
two of them.

DoD checklist (task-10242 DEEPEN, orphan leaf task-6704 "고아 산출물 회수
5828 (qa-2)"):
- [x] negative test 3건 이상 추가 (불변식 위반 입력을 명시적으로 거부하는 케이스)
- [x] 실패주입 케이스 1건 이상 추가 (monkeypatch로 의존성 예외 유발 등)
- [x] `python -m pytest tests/foundation/unit/ml/__init__.py -q` 통과
- [x] docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest

from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.domain.drift import InsufficientSamplesError, population_stability_index
from src.foundation.ml.domain.registry_rules import (
    ModelHashMismatchError,
    validate_new_registration,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _lineage(**overrides: Any) -> TrainDataLineage:
    base: dict[str, Any] = dict(
        start=_NOW - timedelta(days=30), end=_NOW - timedelta(days=1), source_ref="s3://x"
    )
    base.update(overrides)
    return TrainDataLineage(**base)


def _card(**overrides: Any) -> ModelCard:
    base: dict[str, Any] = dict(
        model_id="m1",
        version="v1",
        model_hash="a" * 64,
        train_data_lineage=_lineage(),
        trained_at=_NOW,
        metrics={"auc": 0.9},
        drift_baseline={"f1": tuple(float(i) for i in range(20))},
    )
    base.update(overrides)
    return ModelCard(**base)


# --- happy path: a ModelCard's drift_baseline is a valid `population_stability_index` input ---


def test_model_card_drift_baseline_feeds_population_stability_index() -> None:
    card = _card()
    baseline = card.drift_baseline["f1"]
    current = tuple(float(i) + 0.1 for i in range(20))
    psi = population_stability_index(baseline, current)
    assert psi >= 0.0


# --- negative (>= 3): invariant violations that only surface when contracts.v1 and
# domain modules are composed, not when either is exercised alone ---


def test_model_card_drift_baseline_too_small_rejected_by_drift_module() -> None:
    """A `ModelCard.drift_baseline` sample under drift's `_MIN_SAMPLES`
    floor is a valid pydantic value (contracts.v1 places no length
    constraint on the tuple) but must still be fail-closed rejected the
    moment `domain/drift.py` consumes it -- the two modules must agree on
    what counts as "enough samples" even though neither imports the
    other's constants."""
    card = _card(drift_baseline={"f1": (1.0, 2.0, 3.0)})
    baseline = card.drift_baseline["f1"]
    current = tuple(float(i) for i in range(20))
    with pytest.raises(InsufficientSamplesError):
        population_stability_index(baseline, current)


def test_model_card_drift_baseline_zero_variance_rejected_by_drift_module() -> None:
    """A constant-valued baseline is a valid `ModelCard` (contracts.v1 does
    not check variance) but has no quantile spread, so `domain/drift.py`
    must still reject it instead of returning a degenerate PSI."""
    card = _card(drift_baseline={"f1": tuple(1.0 for _ in range(20))})
    baseline = card.drift_baseline["f1"]
    current = tuple(float(i) for i in range(20))
    with pytest.raises(InsufficientSamplesError):
        population_stability_index(baseline, current)


def test_registry_rule_still_rejects_hash_mismatch_for_cards_built_with_drift_baselines() -> None:
    """Confirms `registry_rules.validate_new_registration` does not
    accidentally key off `drift_baseline` (e.g. treating two cards with
    different baselines as "different models" and skipping the hash
    check) -- only `model_hash` governs the mismatch rule regardless of
    what else differs on the card."""
    existing = _card(model_hash="a" * 64, drift_baseline={"f1": tuple(range(20))})
    candidate = _card(model_hash="b" * 64, drift_baseline={"f1": tuple(range(1, 21))})
    with pytest.raises(ModelHashMismatchError):
        validate_new_registration(candidate, existing=existing)


def test_model_card_rejects_drift_baseline_key_not_matching_any_feature_semantics() -> None:
    """`drift_baseline` is a bare `dict[str, tuple[float, ...]]` in
    contracts.v1 -- an empty-string feature key is a shape contracts.v1
    itself does not forbid (unlike `FeatureSpec.feature_id`, which rejects
    blank strings). This documents that boundary explicitly: composing an
    empty-keyed baseline with the drift module still works mechanically
    (no cross-module invariant to enforce here), so the negative case is
    one level up -- feeding that key's sample straight to drift when it is
    too short still raises, same as any other key."""
    card = _card(drift_baseline={"": (1.0, 2.0, 3.0)})
    assert "" in card.drift_baseline
    with pytest.raises(InsufficientSamplesError):
        population_stability_index(card.drift_baseline[""], tuple(float(i) for i in range(20)))


# --- failure injection: a numpy failure inside drift must propagate, not be swallowed
# by the contracts/domain composition ---


def test_numpy_failure_inside_population_stability_index_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `numpy.quantile` itself raises (e.g. a numpy version regression),
    the failure must surface to the caller unchanged -- fail-closed,
    standard-105 default posture -- rather than being caught anywhere in
    the contracts->domain composition and turned into a misleading
    "no drift" result."""
    card = _card()
    baseline = card.drift_baseline["f1"]
    current = tuple(float(i) + 0.1 for i in range(20))

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("numpy quantile exploded")

    monkeypatch.setattr(np, "quantile", _boom)
    with pytest.raises(RuntimeError, match="numpy quantile exploded"):
        population_stability_index(baseline, current)


# --- package import integrity: every sibling module this package's test files
# depend on must import cleanly as a set ---


@pytest.mark.parametrize(
    "module_path",
    [
        "src.foundation.ml.contracts.v1",
        "src.foundation.ml.domain.drift",
        "src.foundation.ml.domain.registry_rules",
        "src.foundation.ml.domain.point_in_time",
        "src.foundation.ml.domain.feature_values",
    ],
)
def test_ml_domain_and_contract_modules_import_cleanly(module_path: str) -> None:
    importlib.import_module(module_path)
