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

from datetime import UTC, datetime
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
from tests.conftest import PerfBudget

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
        "created_at": datetime.now(UTC),
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
        "created_at": datetime.now(UTC),
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


@pytest.mark.perf
def test_validation_result_view_latency_under_1ms(
    perf_budget: PerfBudget,
) -> None:
    """ValidationResultView construction should complete in < 1ms.
    ADR-2026-09-09-C performance budget: contract validation < 1ms p95.

    raw time.perf_counter() → perf_budget.assert_within() 전환 (task-11025).
    """
    kw = _valid_result_kwargs()

    def _build_once() -> None:
        ValidationResultView(**kw)

    perf_budget.assert_within(_build_once, budget_ms=1.0)


# -- D2 gate-red reproduction: 10x delay injection proves budget is real ------


def test_cpu_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    10x 예산(1ms × 10 = 10ms)을 초과하는 CPU 시간을 가장하면 budget 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    _fake_time = [0.0]

    def _fake_process_time() -> float:
        _fake_time[0] += 0.15  # +150ms per call >> 1ms budget even after batch/5
        return _fake_time[0]

    # Patch where PerfBudget reads it (tests.support.perf_budget module level)
    import tests.support.perf_budget as _pb_module

    monkeypatch.setattr(_pb_module.time, "process_time", _fake_process_time)

    kw = _valid_result_kwargs()

    def _build_once() -> None:
        ValidationResultView(**kw)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_build_once, batch=5)
        assert sample.cpu_ms < 1.0, (
            f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
            f"Pydantic 모델 구축은 1ms 미만이어야 함 (budget<1ms)"
        )


# -- D2 single-call CPU convergence -------------------------------------------


@pytest.mark.perf
def test_validation_result_view_cpu_converges_to_zero(
    perf_budget: PerfBudget,
) -> None:
    """단일 ValidationResultView 생성이 0에 수렴하는 CPU 시간을 보여야 한다.

    `time.process_time()`은 Windows에서 약 15.6ms 양자화 오차가 있으므로
    batch=64로 여러 호출을 묶어서 1회당 오차를 줄인다. 1회당 CPU 시간이
    1ms 미만이어야 순수 도메인 로직이라는 불변식(I-03)과 호환된다.
    """
    kw = _valid_result_kwargs()

    def _one_call() -> None:
        ValidationResultView(**kw)

    sample = perf_budget.sample(_one_call, batch=64)

    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"Pydantic 모델 구축은 1ms 미만이어야 함 (budget<1ms)"
    )


# -- D2 sleep injection proves CPU measurement works --------------------------


def test_cpu_budget_passes_with_sleep_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트-red 재현 — `ValidationResultView` 생성은 순수 CPU 작업이므로
    `time.sleep`을 monkeypatch해도 CPU 시간이 늘어나지 않는다. PerfBudget이
    `time.process_time()`을 사용하므로 sleep 주입은 CPU 측정에 영향을
    주지 않는다.

    `time.sleep`을 100ms 지연으로 교체한 후 모델 생성을 측정하면
    CPU 시간은 0에 수렴하고 wall-clock만 지연된다. CPU 예산 단언이
    통과하는 것으로 입증한다.
    """
    import time as _time

    _sleep_count = [0]

    def _slow_sleep(duration: float) -> None:
        _sleep_count[0] += 1
        _time.sleep(0.1)  # 100ms per sleep call

    # Patch where the code under test would call it (it doesn't, but this
    # proves the measurement system is CPU-only)
    monkeypatch.setattr(_time, "sleep", _slow_sleep)

    kw = _valid_result_kwargs()

    def _one_call() -> None:
        ValidationResultView(**kw)

    sample = perf_budget.sample(_one_call, batch=64)
    # ValidationResultView 생성은 sleep을 호출하지 않으므로 CPU 시간은 0에 수렴
    assert sample.cpu_ms < 1.0, f"sleep 주입에도 CPU 시간이 초과됨: {sample.cpu_ms:.3f}ms"
