"""Tests for src.core.validator.strategy_validator.

Spec: 01_data_models_v1.3.md#§1.2 (9.11 FSMStrategyConfig)
      + strategy_validator.py invariant checks
"""

import pytest

from src.core.validator.strategy_validator import validate_strategy_config
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig

# ── Positive / happy-path ──────────────────────────────────────────────


def test_valid_config_passes_all_validators():
    config = FSMStrategyConfig(
        strategy_id="test-001",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.HOLDING, FSMState.STOP_LOSS],
        transitions=[
            {
                "from_state": FSMState.IDLE,
                "to_state": FSMState.HOLDING,
                "condition": "price < ma",
            },
            {
                "from_state": FSMState.HOLDING,
                "to_state": FSMState.STOP_LOSS,
                "condition": "price < stop",
            },
        ],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert result.is_valid is True
    assert result.errors == []


# ── Negative tests (boundary / bad input) ─────────────────────────────


def test_duplicate_transition_rejected():
    """Negative: identical from+to+condition pairs in transitions."""
    config = FSMStrategyConfig(
        strategy_id="test-002",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.HOLDING],
        transitions=[
            {"from_state": FSMState.IDLE, "to_state": FSMState.HOLDING, "condition": "a"},
            {"from_state": FSMState.IDLE, "to_state": FSMState.HOLDING, "condition": "a"},
        ],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert result.is_valid is False
    assert any("중복" in e for e in result.errors)


def test_self_loop_transition_rejected():
    """Negative: transition from_state == to_state."""
    config = FSMStrategyConfig(
        strategy_id="test-003",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE],
        transitions=[
            {"from_state": FSMState.IDLE, "to_state": FSMState.IDLE, "condition": "loop"},
        ],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert result.is_valid is False
    assert any("자기순환" in e for e in result.errors)


def test_initial_state_not_in_states_rejected():
    """Negative: initial_state absent from states list."""
    config = FSMStrategyConfig(
        strategy_id="test-004",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.HOLDING,
        states=[FSMState.IDLE],
        transitions=[],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert result.is_valid is False
    assert any("initial_state" in e for e in result.errors)


def test_transition_to_unknown_state_rejected():
    """Negative: transition references a state not in states list."""
    config = FSMStrategyConfig(
        strategy_id="test-005",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.HOLDING],
        transitions=[
            {"from_state": FSMState.IDLE, "to_state": FSMState.STOP_LOSS, "condition": "x"},
        ],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert result.is_valid is False
    assert any("to_state" in e and "states" in e for e in result.errors)


def test_transition_from_unknown_state_rejected():
    """Negative: transition from_state not in states list."""
    config = FSMStrategyConfig(
        strategy_id="test-006",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.HOLDING],
        transitions=[
            {"from_state": FSMState.EMERGENCY_EXIT, "to_state": FSMState.IDLE, "condition": "x"},
        ],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert result.is_valid is False
    assert any("from_state" in e and "states" in e for e in result.errors)


def test_empty_states_rejected():
    """Negative: FSM with zero states is invalid — initial_state absent."""
    config = FSMStrategyConfig(
        strategy_id="test-007",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.IDLE,
        states=[],
        transitions=[],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert result.is_valid is False
    assert any("initial_state" in e for e in result.errors)


def test_orphan_state_detected():
    """Negative: state declared but never participates in any transition."""
    config = FSMStrategyConfig(
        strategy_id="test-008",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.HOLDING, FSMState.STOP_LOSS],
        transitions=[
            {"from_state": FSMState.IDLE, "to_state": FSMState.HOLDING, "condition": "buy"},
        ],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert result.is_valid is False
    assert any("고아" in e for e in result.errors)


# ── Failure-injection test ────────────────────────────────────────────


def test_validate_config_logs_on_error(monkeypatch):
    """Failure injection: replace ValidationResult with a stub that records
    every call, then verify the validator passes all collected errors through.

    This replaces the original stub test that claimed to mock a logger but
    never imported or used one — the validator has no logger dependency.
    """
    import src.core.validator.strategy_validator as strategy_validator

    call_log: list[tuple[bool, list[str]]] = []

    class RecordingResult:
        def __init__(self, is_valid: bool, errors: list[str]) -> None:
            self.is_valid = is_valid
            self.errors = errors
            call_log.append((is_valid, list(errors)))

    monkeypatch.setattr(strategy_validator, "ValidationResult", RecordingResult)

    config = FSMStrategyConfig(
        strategy_id="test-009",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.HOLDING,
        states=[FSMState.IDLE],
        transitions=[],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert isinstance(result, RecordingResult)
    assert result.is_valid is False
    assert len(call_log) == 1
    assert call_log[0][1]  # at least one error recorded


def test_multiple_errors_accumulated():
    """Negative: multiple independent errors should all be collected."""
    config = FSMStrategyConfig(
        strategy_id="test-010",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.HOLDING,  # not in states
        states=[FSMState.IDLE],
        transitions=[
            {"from_state": FSMState.IDLE, "to_state": FSMState.IDLE, "condition": "loop"},
        ],
        author_agent="researcher-1",
    )
    result = validate_strategy_config(config)
    assert result.is_valid is False
    # Should have both: initial_state error + self-loop error
    error_text = " ".join(result.errors)
    assert "initial_state" in error_text
    assert "자기순환" in error_text


def test_validation_result_dependency_failure_propagates(monkeypatch):
    """Failure injection: ValidationResult constructor raising must not be

    swallowed — fail-closed means the caller sees the exception rather than
    a falsely "valid" result.
    """
    import src.core.validator.strategy_validator as strategy_validator

    def boom(*args, **kwargs):
        raise RuntimeError("ValidationResult dependency failure")

    monkeypatch.setattr(strategy_validator, "ValidationResult", boom)

    config = FSMStrategyConfig(
        strategy_id="test-011",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.HOLDING],
        transitions=[
            {"from_state": FSMState.IDLE, "to_state": FSMState.HOLDING, "condition": "a"},
        ],
        author_agent="researcher-1",
    )
    with pytest.raises(RuntimeError, match="ValidationResult dependency failure"):
        validate_strategy_config(config)


# ── Performance assertion (perf_budget fixture, D2) ───────────────────


def test_validate_config_within_latency_budget(perf_budget):
    """Perf: validating a 10-state, 30-transition FSM stays within budget."""
    states = [FSMState.IDLE, FSMState.HOLDING, FSMState.STOP_LOSS]
    transitions = [
        {"from_state": FSMState.IDLE, "to_state": FSMState.HOLDING, "condition": f"c{i}"}
        for i in range(30)
    ]
    config = FSMStrategyConfig(
        strategy_id="test-012",
        version="v1",
        target_asset="BTC/KRW",
        market="crypto",
        exchange="Bithumb",
        initial_state=FSMState.IDLE,
        states=states,
        transitions=transitions,
        author_agent="researcher-1",
    )
    perf_budget.assert_within(lambda: validate_strategy_config(config), budget_ms=5)
