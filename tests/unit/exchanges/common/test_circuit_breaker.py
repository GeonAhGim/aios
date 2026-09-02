"""L4-11 — circuit_breaker 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11
DoD: 임계 초과 OPEN, open_sec 후 HALF_OPEN, half-open 실패 재OPEN.
"""
from __future__ import annotations

from src.exchanges.common.circuit_breaker import CircuitState, VenueCircuit


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _make_circuit(**kwargs: object) -> tuple[VenueCircuit, _FakeClock]:
    clock = _FakeClock()
    circuit = VenueCircuit(clock=clock, **kwargs)
    return circuit, clock


def test_starts_closed_and_allows_calls() -> None:
    circuit, _clock = _make_circuit()
    assert circuit.state == CircuitState.CLOSED
    assert circuit.allow() is True


def test_failure_threshold_exceeded_opens_circuit() -> None:
    circuit, _clock = _make_circuit(failure_threshold=3, window_sec=30.0)
    for _ in range(3):
        assert circuit.allow() is True
        circuit.record(ok=False)
    assert circuit.state == CircuitState.OPEN
    assert circuit.allow() is False


def test_failures_outside_window_do_not_count() -> None:
    circuit, clock = _make_circuit(failure_threshold=3, window_sec=10.0)
    circuit.record(ok=False)
    clock.now += 20.0  # 윈도우 밖으로 밀려남
    circuit.record(ok=False)
    circuit.record(ok=False)
    assert circuit.state == CircuitState.CLOSED


def test_open_transitions_to_half_open_after_open_sec() -> None:
    circuit, clock = _make_circuit(failure_threshold=2, open_sec=20.0)
    circuit.record(ok=False)
    circuit.record(ok=False)
    assert circuit.state == CircuitState.OPEN

    clock.now += 19.9
    assert circuit.allow() is False
    assert circuit.state == CircuitState.OPEN

    clock.now += 0.2
    assert circuit.allow() is True
    assert circuit.state == CircuitState.HALF_OPEN


def test_half_open_failure_reopens_circuit() -> None:
    circuit, clock = _make_circuit(failure_threshold=2, open_sec=20.0, half_open_max=2)
    circuit.record(ok=False)
    circuit.record(ok=False)
    clock.now += 20.0
    assert circuit.allow() is True  # HALF_OPEN 시행 호출 허용

    circuit.record(ok=False)
    assert circuit.state == CircuitState.OPEN
    assert circuit.allow() is False  # 재오픈 직후에는 바로 재시행 불가


def test_half_open_success_up_to_max_closes_circuit() -> None:
    circuit, clock = _make_circuit(failure_threshold=2, open_sec=20.0, half_open_max=2)
    circuit.record(ok=False)
    circuit.record(ok=False)
    clock.now += 20.0

    assert circuit.allow() is True
    circuit.record(ok=True)
    assert circuit.state == CircuitState.HALF_OPEN

    assert circuit.allow() is True
    circuit.record(ok=True)
    assert circuit.state == CircuitState.CLOSED
    assert circuit.allow() is True


def test_half_open_blocks_calls_beyond_half_open_max() -> None:
    circuit, clock = _make_circuit(failure_threshold=1, open_sec=20.0, half_open_max=1)
    circuit.record(ok=False)
    clock.now += 20.0
    assert circuit.allow() is True
    assert circuit.allow() is False  # half_open_max=1 소진, 결과 대기중
