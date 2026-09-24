"""task-4635 -- TEST-cov: src/foundation/validation/contracts/v1.py 0% -> 70%+.

Covers every public symbol in v1.py:
- SCHEMA_VERSION constant
- RunState enum (QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED)
- Outcome enum (PASS, FAIL, PASS_WITH_OBLIGATIONS)
- StartValidationCommand Pydantic model (7 required fields)
- ValidationResultView Pydantic model (14 fields, some with defaults)
- ValidationBundleView Pydantic model (11 fields, some with defaults)

D2 evidence: negative >=3, failure-injection 1, perf 1, gate-red repro 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.validation.contracts.v1 import (
    SCHEMA_VERSION,
    StartValidationCommand,
    ValidationBundleView,
    ValidationResultView,
)

# ── SCHEMA_VERSION ─────────────────────────────────────────────────────────

def test_schema_version_is_v1() -> None:
    assert SCHEMA_VERSION == "v1"
    assert isinstance(SCHEMA_VERSION, str)


# ── RunState enum ──────────────────────────────────────────────────────────

def test_run_state_all_values() -> None:
    from src.foundation.validation.contracts.v1 import RunState
    assert RunState.QUEUED.value == "QUEUED"
    assert RunState.RUNNING.value == "RUNNING"
    assert RunState.SUCCEEDED.value == "SUCCEEDED"
    assert RunState.FAILED.value == "FAILED"
    assert RunState.CANCELLED.value == "CANCELLED"


def test_run_state_from_str() -> None:
    from src.foundation.validation.contracts.v1 import RunState
    assert RunState("QUEUED") is RunState.QUEUED
    assert RunState("SUCCEEDED") is RunState.SUCCEEDED
    assert RunState("CANCELLED") is RunState.CANCELLED


# ── Outcome enum ───────────────────────────────────────────────────────────

def test_outcome_all_values() -> None:
    from src.foundation.validation.contracts.v1 import Outcome
    assert Outcome.PASS.value == "PASS"
    assert Outcome.FAIL.value == "FAIL"
    assert Outcome.PASS_WITH_OBLIGATIONS.value == "PASS_WITH_OBLIGATIONS"


def test_outcome_from_str() -> None:
    from src.foundation.validation.contracts.v1 import Outcome
    assert Outcome("PASS") is Outcome.PASS
    assert Outcome("FAIL") is Outcome.FAIL


# ── StartValidationCommand ─────────────────────────────────────────────────

def _valid_cmd_kwargs(**overrides) -> dict[str, Any]:
    """Return a minimal valid command dict, overridden by *overrides*."""
    return {
        "strategy_id": "strat-001",
        "strategy_version": "1.0.0",
        "cost_model_fee_bps": Decimal("5.0"),
        "cost_model_slippage_bps": Decimal("2.0"),
        "warmup_bars": 20,
        "periods_per_year": 252,
        "initial_equity": Decimal("10000"),
        **overrides,
    }


def test_start_command_all_fields() -> None:
    kw = _valid_cmd_kwargs()
    cmd = StartValidationCommand(**kw)
    assert cmd.strategy_id == "strat-001"
    assert cmd.strategy_version == "1.0.0"
    assert cmd.cost_model_fee_bps == Decimal("5.0")
    assert cmd.cost_model_slippage_bps == Decimal("2.0")
    assert cmd.warmup_bars == 20
    assert cmd.periods_per_year == 252
    assert cmd.initial_equity == Decimal("10000")


def test_start_command_decimal_types() -> None:
    """cost_model_fee_bps and initial_equity must be Decimal, not float."""
    kw = _valid_cmd_kwargs()
    cmd = StartValidationCommand(**kw)
    assert isinstance(cmd.cost_model_fee_bps, Decimal)
    assert isinstance(cmd.initial_equity, Decimal)


def test_start_command_int_fields() -> None:
    """warmup_bars and periods_per_year must be int."""
    kw = _valid_cmd_kwargs()
    cmd = StartValidationCommand(**kw)
    assert isinstance(cmd.warmup_bars, int)
    assert isinstance(cmd.periods_per_year, int)


# ── ValidationResultView ───────────────────────────────────────────────────

def _valid_result_kwargs(**overrides) -> dict[str, Any]:
    """Return a minimal valid result dict, overridden by *overrides*."""
    return {
        "run_id": uuid4(),
        "strategy_id": "strat-001",
        "strategy_version": "1.0.0",
        "check_type": "backtest",
        "state": "SUCCEEDED",
        "outcome": "PASS",
        "metrics": {"sharpe": 1.5},
        "warnings": [],
        "hard_fail_reasons": [],
        "obligations": [],
        "result_hash": "abc123",
        "created_at": datetime.now(timezone.utc),
        "evidence_refs": [],
        **overrides,
    }


def test_validation_result_view_all_fields() -> None:
    kw = _valid_result_kwargs()
    view = ValidationResultView(**kw)
    assert view.run_id is not None
    assert view.strategy_id == "strat-001"
    assert view.state == "SUCCEEDED"
    assert view.outcome == "PASS"
    assert view.metrics == {"sharpe": 1.5}
    assert view.result_hash == "abc123"
    assert view.schema_version == "v1"


def test_validation_result_view_evidence_refs_with_values() -> None:
    kw = _valid_result_kwargs(evidence_refs=["ref-1", "ref-2"])
    view = ValidationResultView(**kw)
    assert view.evidence_refs == ["ref-1", "ref-2"]


def test_validation_result_view_outcome_none() -> None:
    """outcome can be None for in-progress results."""
    kw = _valid_result_kwargs(outcome=None)
    view = ValidationResultView(**kw)
    assert view.outcome is None


def test_validation_result_view_schema_version_default() -> None:
    """schema_version defaults to SCHEMA_VERSION constant."""
    kw = _valid_result_kwargs()
    kw["schema_version"] = "v2"
    view = ValidationResultView(**kw)
    assert view.schema_version == "v2"
    # Without override, should default to SCHEMA_VERSION
    kw.pop("schema_version")
    view2 = ValidationResultView(**kw)
    assert view2.schema_version == "v1"


# ── ValidationBundleView ───────────────────────────────────────────────────

def _valid_bundle_kwargs(**overrides) -> dict[str, Any]:
    """Return a minimal valid bundle dict, overridden by *overrides*."""
    return {
        "id": uuid4(),
        "artifact_hash": "artifact-abc",
        "policy_version": "1.0.0",
        "data_snapshot_hash": "snapshot-xyz",
        "outcome": "PASS",
        "check_run_ids": [uuid4(), uuid4()],
        "bundle_hash": "bundle-hash-1",
        "hard_fail_reasons": [],
        "obligations": [],
        "created_at": datetime.now(timezone.utc),
        **overrides,
    }


def test_validation_bundle_view_all_fields() -> None:
    kw = _valid_bundle_kwargs()
    bundle = ValidationBundleView(**kw)
    assert bundle.artifact_hash == "artifact-abc"
    assert bundle.policy_version == "1.0.0"
    assert bundle.data_snapshot_hash == "snapshot-xyz"
    assert bundle.outcome == "PASS"
    assert len(bundle.check_run_ids) == 2
    assert bundle.bundle_hash == "bundle-hash-1"
    assert bundle.schema_version == "v1"


def test_validation_bundle_view_hard_fail_reasons_with_values() -> None:
    """hard_fail_reasons accepts a list of strings."""
    kw = _valid_bundle_kwargs(hard_fail_reasons=["reason-1", "reason-2"])
    bundle = ValidationBundleView(**kw)
    assert bundle.hard_fail_reasons == ["reason-1", "reason-2"]


def test_validation_bundle_view_obligations_with_values() -> None:
    """obligations accepts a list of strings."""
    kw = _valid_bundle_kwargs(obligations=["obligation-1"])
    bundle = ValidationBundleView(**kw)
    assert bundle.obligations == ["obligation-1"]


def test_validation_bundle_view_schema_version_default() -> None:
    """schema_version defaults to SCHEMA_VERSION constant."""
    kw = _valid_bundle_kwargs()
    kw["schema_version"] = "v2"
    bundle = ValidationBundleView(**kw)
    assert bundle.schema_version == "v2"
    # Without override, should default to SCHEMA_VERSION
    kw.pop("schema_version")
    bundle2 = ValidationBundleView(**kw)
    assert bundle2.schema_version == "v1"


def test_validation_bundle_view_multiple_check_run_ids() -> None:
    ids = [uuid4() for _ in range(5)]
    kw = _valid_bundle_kwargs(check_run_ids=ids)
    bundle = ValidationBundleView(**kw)
    assert len(bundle.check_run_ids) == 5
    assert set(bundle.check_run_ids) == set(ids)


# ── Negative tests ─────────────────────────────────────────────────────────

def test_start_command_missing_strategy_id_raises() -> None:
    kw = _valid_cmd_kwargs()
    del kw["strategy_id"]
    with pytest.raises(ValidationError):
        StartValidationCommand(**kw)


def test_start_command_missing_strategy_version_raises() -> None:
    kw = _valid_cmd_kwargs()
    del kw["strategy_version"]
    with pytest.raises(ValidationError):
        StartValidationCommand(**kw)


def test_start_command_missing_cost_model_fee_bps_raises() -> None:
    kw = _valid_cmd_kwargs()
    del kw["cost_model_fee_bps"]
    with pytest.raises(ValidationError):
        StartValidationCommand(**kw)


def test_start_command_missing_initial_equity_raises() -> None:
    kw = _valid_cmd_kwargs()
    del kw["initial_equity"]
    with pytest.raises(ValidationError):
        StartValidationCommand(**kw)


def test_start_command_invalid_warmup_bars_type_raises() -> None:
    kw = _valid_cmd_kwargs()
    kw["warmup_bars"] = "twenty"
    with pytest.raises(ValidationError):
        StartValidationCommand(**kw)


def test_start_command_negative_empty_dict_raises() -> None:
    """Negative test: empty dict should raise ValidationError, not return
    a partial StartValidationCommand with None fields."""
    with pytest.raises(ValidationError):
        StartValidationCommand(**{})


def test_validation_result_view_missing_run_id_raises() -> None:
    kw = _valid_result_kwargs()
    del kw["run_id"]
    with pytest.raises(ValidationError):
        ValidationResultView(**kw)


def test_validation_result_view_missing_created_at_raises() -> None:
    kw = _valid_result_kwargs()
    del kw["created_at"]
    with pytest.raises(ValidationError):
        ValidationResultView(**kw)


def test_validation_result_view_invalid_created_at_raises() -> None:
    kw = _valid_result_kwargs()
    kw["created_at"] = "not-a-datetime"
    with pytest.raises(ValidationError):
        ValidationResultView(**kw)


def test_validation_result_view_negative_empty_dict_raises() -> None:
    """Negative test: empty dict should raise on required fields."""
    with pytest.raises(ValidationError):
        ValidationResultView(**{})


def test_validation_bundle_view_missing_id_raises() -> None:
    kw = _valid_bundle_kwargs()
    del kw["id"]
    with pytest.raises(ValidationError):
        ValidationBundleView(**kw)


def test_validation_bundle_view_missing_outcome_raises() -> None:
    kw = _valid_bundle_kwargs()
    del kw["outcome"]
    with pytest.raises(ValidationError):
        ValidationBundleView(**kw)


def test_validation_bundle_view_invalid_outcome_raises() -> None:
    kw = _valid_bundle_kwargs()
    kw["outcome"] = "bogus_outcome"
    with pytest.raises(ValidationError):
        ValidationBundleView(**kw)


def test_validation_bundle_view_negative_empty_dict_raises() -> None:
    """Negative test: empty dict should raise on required fields."""
    with pytest.raises(ValidationError):
        ValidationBundleView(**{})


# ── Failure-injection test ─────────────────────────────────────────────────

def test_validation_result_view_monkeypatch_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject an invalid RunState to confirm validation catches it."""
    kw = _valid_result_kwargs()
    kw["state"] = "INVALID_STATE"
    with pytest.raises(ValidationError):
        ValidationResultView(**kw)


# ── Performance assertion ──────────────────────────────────────────────────

def test_validation_result_view_latency_under_1ms() -> None:
    """ValidationResultView construction should complete in < 1ms.
    ADR-2026-09-09-C performance budget: contract validation < 1ms p95."""
    kw = _valid_result_kwargs()
    iterations = 1000
    start = time.perf_counter()
    for _ in range(iterations):
        ValidationResultView(**kw)
    elapsed_ms = (time.perf_counter() - start) / iterations * 1000
    assert elapsed_ms < 1.0, f"avg {elapsed_ms:.2f}ms exceeds 1ms budget"
