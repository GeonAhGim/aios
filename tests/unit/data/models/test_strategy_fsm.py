from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from tests.conftest import PerfBudget


def _valid_kwargs(**overrides):
    kwargs = dict(
        strategy_id="strat-1",
        version="v1.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        states=[FSMState.IDLE, FSMState.HOLDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="buy_signal"
            )
        ],
        author_agent="strategy-research-agent",
    )
    kwargs.update(overrides)
    return kwargs


def test_fsm_strategy_config_roundtrip():
    config = FSMStrategyConfig(**_valid_kwargs())
    assert config.initial_state == FSMState.IDLE
    assert config.memory_provenance == []
    assert config.transitions[0].to_state == FSMState.HOLDING


def test_fsm_strategy_config_with_explicit_memory_provenance():
    provenance_id = uuid4()
    config = FSMStrategyConfig(**_valid_kwargs(memory_provenance=[provenance_id]))
    assert config.memory_provenance == [provenance_id]


def test_invalid_initial_state_enum_value_raises():
    with pytest.raises(ValidationError):
        FSMStrategyConfig(**_valid_kwargs(initial_state="NOT_A_REAL_STATE"))


def test_missing_required_field_raises():
    kwargs = _valid_kwargs()
    del kwargs["author_agent"]
    with pytest.raises(ValidationError):
        FSMStrategyConfig(**kwargs)


def test_transition_with_invalid_state_type_raises():
    with pytest.raises(ValidationError):
        FSMTransition(from_state="NOT_A_REAL_STATE", to_state=FSMState.HOLDING, condition="x")


def test_memory_provenance_invalid_uuid_raises():
    with pytest.raises(ValidationError):
        FSMStrategyConfig(**_valid_kwargs(memory_provenance=["not-a-uuid"]))


def test_transition_dependency_failure_propagates_fail_closed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(FSMTransition, "__init__", boom)
    with pytest.raises(RuntimeError):
        FSMTransition(from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="buy_signal")


@pytest.mark.perf
def test_fsm_strategy_config_construction_throughput(perf_budget: PerfBudget) -> None:
    """FSMStrategyConfig construction throughput — budget: 5 ms/op (process_time).

    `perf_budget.assert_within` uses `time.process_time()` (not wall-clock
    `perf_counter`) so CI xdist scheduler noise and other-process CPU contention
    do not inflate the measurement. `batch=16` groups fast calls so the total
    time exceeds the ~15.6 ms process_time tick, reducing quantisation error.
    """
    sample = perf_budget.assert_within(
        lambda: FSMStrategyConfig(**_valid_kwargs()),
        budget_ms=5.0,
        n=5,
        batch=16,
    )
    assert sample.cpu_ms < 5.0


# --- 게이트 적색 재현 (1): 10x 지연 주입으로 budget 단언이 적색인지 검증 --------


@pytest.mark.perf
def test_fsm_strategy_config_construction_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch,
):
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. time.process_time을 monkeypatch해 매 호출당
    1초씩 증가하도록 하면 cpu_ms가 예산(5ms)을 크게 초과해 AssertionError가
    발생해야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    # PerfBudget.sample() 내부에서 time.process_time()을 호출하므로
    # 이를 monkeypatch해 매 호출마다 1초씩 증가하도록 한다.
    # cpu_ms = (end - start) * 1000 / batch = 1000ms >> 5ms budget.
    _call_count = [0]

    def _fake_process_time() -> float:
        _call_count[0] += 1
        return float(_call_count[0])

    # Patch where PerfBudget reads it (tests.support.perf_budget module level)
    import tests.support.perf_budget as _pb_module

    monkeypatch.setattr(_pb_module.time, "process_time", _fake_process_time)

    def _build_once() -> None:
        FSMStrategyConfig(**_valid_kwargs())

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        samples = perf_budget.samples(
            _build_once,
            n=5,
            batch=16,
        )
        cpu_ms_values = sorted(s.cpu_ms for s in samples)
        assert cpu_ms_values[-1] < 5.0, f"p99={cpu_ms_values[-1]:.3f}ms — FSM 구성 예산 초과"
