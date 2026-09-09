"""L4-11 — circuit_breaker 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11
DoD: 임계 초과 OPEN, open_sec 후 HALF_OPEN, half-open 실패 재OPEN.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter

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


def test_allow_and_record_latency_budget() -> None:
    """`allow()`/`record()`는 요청마다 호출되는 hot path — 로컬 회귀 예산이며
    SLO 단언은 아니다(headless worker 지침). 정상 흐름(성공 응답이 대부분인
    실제 트래픽 패턴)을 측정한다 — 실패 목록 스캔의 최악 성능은 window_sec
    자체가 상한을 두는 별개 특성이라 여기서는 다루지 않는다."""
    circuit, clock = _make_circuit(failure_threshold=1_000_000, window_sec=1.0)
    circuit.allow()  # 콜드 스타트 워밍업 — 예산 밖.
    started = perf_counter()
    for _ in range(100_000):
        clock.now += 0.001
        circuit.allow()
        circuit.record(ok=True)
    elapsed = perf_counter() - started
    assert elapsed < 3.0, f"100000 allow+record cycles took {elapsed:.3f}s (budget 3.0s)"


def test_pytest_gate_turns_red_when_threshold_check_is_removed(tmp_path: Path) -> None:
    """실제 임계 초과 OPEN 전이 테스트가 통과하는 걸 먼저 확인하고, 임계값
    검사를 무력화하면(=회로가 절대 열리지 않으면) pytest가 exit 1로 red가
    되는 것까지 증명한다(gate/CI red-line regression proof, DEPTH_L4_BR
    task-456 D2 미달 사유 해소)."""
    test_copy = tmp_path / "test_circuit_breaker_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-c", str(config), "--confcutdir", str(tmp_path),
        f"{test_copy}::test_failure_threshold_exceeded_opens_circuit",
    ]
    env = dict(
        os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8"
    )
    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=60, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    # 프로덕션 소스 파일은 그대로 두고, 이 자식 프로세스의 모듈 객체만 변조한다.
    (tmp_path / "conftest.py").write_text(
        "import importlib\nfrom pathlib import Path\n"
        "name = 'src.exchanges.common.circuit_breaker'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = '            if len(self._failure_times) >= self._failure_threshold:'\n"
        "assert source.count(guard) == 1\n"
        "mutated_src = source.replace(guard, '            if False:')\n"
        "mutant = compile(mutated_src, module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=60, check=False,
    )
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "1 failed" in mutated.stdout


def test_two_independent_venue_circuits_do_not_share_state() -> None:
    """다중 인스턴스 증명(D3) — `VenueCircuit`은 venue 단위로 하나씩 만들어진다
    (docstring). 한 거래소(예: bitget)가 실패로 OPEN돼도 다른 거래소(예:
    binance)의 회로는 전혀 영향을 받지 않아야 한다."""
    bitget, bitget_clock = _make_circuit(failure_threshold=2, open_sec=20.0)
    binance, _binance_clock = _make_circuit(failure_threshold=2, open_sec=20.0)

    bitget.record(ok=False)
    bitget.record(ok=False)
    assert bitget.state == CircuitState.OPEN
    assert bitget.allow() is False

    # binance는 완전히 별개 인스턴스이므로 CLOSED 상태를 유지한다.
    assert binance.state == CircuitState.CLOSED
    assert binance.allow() is True


def test_adversarial_alternating_success_failure_at_window_boundary_does_not_open() -> None:
    """적대적 증명(D3) — 실패를 임계값 바로 아래로 유지하면서 창(window) 경계를
    넘나드는 패턴으로 흔드는 공격(경계 조건을 노려 오탐/누락을 유발하려는
    시도)에도 실제 임계 미만이면 절대 OPEN되지 않아야 한다."""
    circuit, clock = _make_circuit(failure_threshold=3, window_sec=10.0)
    for _ in range(20):
        circuit.record(ok=False)
        assert circuit.state == CircuitState.CLOSED
        circuit.record(ok=True)  # 성공을 끼워 넣어도 실패 카운트는 그대로 누적되지 않음
        clock.now += 9.9  # 매번 창 경계 바로 안쪽까지만 전진
    assert circuit.state == CircuitState.CLOSED


async def test_concurrent_asyncio_workers_respect_half_open_max() -> None:
    """적대적 동시성 증명(D3) — 여러 OMS 워커(코루틴)가 동시에 재개를 시도하는
    실제 시나리오를 흉내낸다. `allow()`/`record()` 자체는 내부에 await가
    없어 단일 스레드 asyncio 안에서 원자적으로 실행되지만, 호출 사이에 실제
    네트워크 I/O를 흉내낸 `await`를 끼워 넣은 여러 워커가 뒤섞여도
    `half_open_max`를 넘는 시행이 허용되지 않는지 증명한다."""
    circuit, clock = _make_circuit(failure_threshold=2, open_sec=20.0, half_open_max=2)
    circuit.record(ok=False)
    circuit.record(ok=False)
    clock.now += 20.0
    assert circuit.state == CircuitState.OPEN

    granted: list[bool] = []

    async def _worker() -> None:
        permitted = circuit.allow()
        await asyncio.sleep(0)  # 실제 네트워크 호출을 흉내낸 인터리빙 지점.
        granted.append(permitted)
        if permitted:
            circuit.record(ok=True)

    await asyncio.gather(*(_worker() for _ in range(10)))
    assert granted.count(True) <= 2
