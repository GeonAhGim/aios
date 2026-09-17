"""L37 -- unit tests for `domain/models.py`'s artifact-linkage additions
(`ValidationRun.artifact_hash`/`policy_version`/`seed`/`data_snapshot_hash`/
`trace_id`, `ValidationResult.evidence_refs`, and the new `ValidationBundle`).
Pure domain, no DB. D2 evidence (ADR-2026-09-09-C Decision 1): negative >=3,
failure injection 1, perf assertion 1, gate-red repro 1.
"""

from __future__ import annotations

import time
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from scripts.check_import_linter import ROOT as LINTER_ROOT
from scripts.check_import_linter import _eval_forbidden_suffix, _imports_of, parse_contracts
from src.foundation.validation.domain.models import (
    Outcome,
    RunState,
    ValidationBundle,
    ValidationResult,
    ValidationRun,
)

_DOMAIN_DIR = Path(__file__).resolve().parents[4] / "src" / "foundation" / "validation" / "domain"


def _run(**overrides):
    kwargs = dict(
        id=uuid4(),
        strategy_id="strat-1",
        strategy_version="v1",
        check_type="backtest",
        input_snapshot_hash="deadbeef",
        cost_model={"fee_bps": "10"},
        warmup_bars=20,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
        state=RunState.QUEUED,
    )
    kwargs.update(overrides)
    return ValidationRun(**kwargs)


def _bundle(**overrides):
    kwargs = dict(
        id=uuid4(),
        artifact_hash="artifact-hash-1",
        policy_version="vp-v1",
        data_snapshot_hash="snapshot-hash-1",
        outcome=Outcome.PASS,
        check_run_ids=(uuid4(), uuid4()),
        bundle_hash="bundle-hash-1",
    )
    kwargs.update(overrides)
    return ValidationBundle(**kwargs)


# --------------------------------------------------------------------------
# Backward compatibility -- pre-L37 callers (start_validation.py,
# postgres_repository.py's _row_to_run/_row_to_result) never pass the new
# fields; they must keep constructing unchanged.
# --------------------------------------------------------------------------


def test_validation_run_constructs_without_artifact_linkage_fields():
    run = _run()
    assert run.artifact_hash is None
    assert run.policy_version == "vp-v1"
    assert run.seed == 0
    assert run.data_snapshot_hash is None
    assert run.trace_id is None


def test_validation_result_constructs_without_evidence_refs():
    result = ValidationResult(
        id=uuid4(), run_id=uuid4(), outcome=Outcome.PASS, metrics={"sharpe_annualized": "1.2"}
    )
    assert result.evidence_refs == ()


def test_validation_run_accepts_artifact_linkage_fields():
    run = _run(
        artifact_hash="artifact-hash-1",
        policy_version="vp-v1",
        seed=42,
        data_snapshot_hash="snapshot-hash-1",
        trace_id="trace-1",
    )
    assert run.artifact_hash == "artifact-hash-1"
    assert run.seed == 42


def test_validation_result_accepts_evidence_refs():
    result = ValidationResult(
        id=uuid4(),
        run_id=uuid4(),
        outcome=Outcome.PASS,
        metrics={},
        evidence_refs=("snapshot:deadbeef", "artifact:artifact-hash-1"),
    )
    assert result.evidence_refs == ("snapshot:deadbeef", "artifact:artifact-hash-1")


def test_validation_bundle_constructs_with_matching_outcome_and_reasons():
    bundle = _bundle()
    assert bundle.outcome == Outcome.PASS
    assert bundle.hard_fail_reasons == ()


# --------------------------------------------------------------------------
# D2 negative (>=3) -- I6 both directions, and frozen-instance immutability.
# --------------------------------------------------------------------------


def test_validation_bundle_rejects_hard_fail_reasons_without_fail_outcome():
    with pytest.raises(ValueError, match="I6 violation"):
        _bundle(outcome=Outcome.PASS, hard_fail_reasons=("INTEGRITY_FUTURE_DATA",))


def test_validation_bundle_rejects_fail_outcome_without_hard_fail_reasons():
    with pytest.raises(ValueError, match="I6 violation"):
        _bundle(outcome=Outcome.FAIL, hard_fail_reasons=())


def test_validation_run_is_frozen_rejects_mutation():
    run = _run()
    with pytest.raises(FrozenInstanceError):
        cast(Any, run).seed = 7


def test_validation_bundle_is_frozen_rejects_mutation():
    bundle = _bundle()
    with pytest.raises(FrozenInstanceError):
        cast(Any, bundle).outcome = Outcome.FAIL


# --------------------------------------------------------------------------
# D2 failure injection -- simulate the exact aggregation bug §4.1 I6 exists
# to catch: a future `build_bundle.py` (L43) unions `hard_fail_reasons`
# across checks correctly but then trusts a stale/mislabeled `outcome`
# variable instead of re-deriving it (the same class of bug
# `test_rules.test_bundle_ignores_mislabeled_per_check_outcome_field`
# guards inside `evaluate_bundle` itself). `ValidationBundle` must refuse
# to be constructed with that mismatch rather than silently persisting a
# PASS bundle that actually hard-failed.
# --------------------------------------------------------------------------


def test_validation_bundle_construction_catches_stale_outcome_after_hard_fail_union():
    hard_fail_reasons_from_checks = ["INTEGRITY_LINEAGE_MISSING", "VALIDATION_OOS_INSUFFICIENT"]
    stale_outcome = Outcome.PASS_WITH_OBLIGATIONS  # bug: computed before the union above

    with pytest.raises(ValueError, match="I6 violation"):
        _bundle(
            outcome=stale_outcome,
            hard_fail_reasons=tuple(hard_fail_reasons_from_checks),
        )


# --------------------------------------------------------------------------
# D2 성능 단언 -- ADR-2026-09-09-C 예산표 "DSL 컴파일 300ms"를 이 leaf에 적용.
# run_check()가 매 체크마다 ValidationRun/ValidationBundle을 구성하므로,
# 10,000회 반복 구성이 그 예산 안에서 끝남을 확인한다.
# --------------------------------------------------------------------------


def test_validation_run_and_bundle_construction_within_dsl_compile_budget():
    start = time.perf_counter()
    for _ in range(10_000):
        _run()
        _bundle()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.3, f"10,000x construction took {elapsed * 1000:.2f}ms, budget 300ms"


# --------------------------------------------------------------------------
# D2 게이트 적색 재현 -- .importlinter domain-no-adapters 계약이 models.py의
# 순수성(같은 애그리게잇의 adapters/를 임포트할 수 없음)을 실제로 집행하는지,
# 합성 회귀 그래프가 적색으로 잡히는지와 실제 현재 import는 녹색인지 대조
# 증명한다(test_policy_hash.py가 policy/check_result/artifact에 대해 쓰는
# 것과 동일 패턴, models.py로 확장).
# --------------------------------------------------------------------------


def test_import_linter_domain_no_adapters_catches_models_regression():
    contracts = parse_contracts(LINTER_ROOT / ".importlinter")
    domain_no_adapters = next(c for c in contracts if c["id"] == "domain-no-adapters")

    regressed_graph = {
        "src.foundation.validation.domain.models": {
            "src.foundation.validation.adapters.postgres_repository"
        }
    }
    hits = _eval_forbidden_suffix(regressed_graph, domain_no_adapters)
    assert len(hits) == 1
    assert hits[0][0] == "src.foundation.validation.domain.models"

    real_graph = {
        "src.foundation.validation.domain.models": _imports_of(
            _DOMAIN_DIR / "models.py", "src.foundation.validation.domain.models", False
        )
    }
    assert _eval_forbidden_suffix(real_graph, domain_no_adapters) == []
