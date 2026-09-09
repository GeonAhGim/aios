"""L4-11 — clock_sync 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11
DoD: 왕복 보정, skew 초과 차단.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter

import pytest

from src.exchanges.common.clock_sync import ServerClock
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind


class _FakeClock:
    """호출할 때마다 미리 정해진 값을 순서대로 반환한다(t0, t1, t0, t1, ...)."""

    def __init__(self, ticks: list[float]) -> None:
        self._ticks = list(ticks)
        self._idx = 0

    def __call__(self) -> float:
        value = self._ticks[self._idx]
        self._idx += 1
        return value


async def test_sync_applies_half_round_trip_correction() -> None:
    # t0=1000, 서버가 1100을 보고함, t1=1040 → round_trip=40, 절반=20
    # estimated_server_now = 1100 + 20 = 1120, offset = 1120 - 1040 = 80
    clock = _FakeClock([1000.0, 1040.0])
    server_clock = ServerClock(max_skew_ms=1000, clock=clock)

    async def fetch_server_ms() -> int:
        return 1100

    await server_clock.sync(fetch_server_ms)
    assert server_clock.offset_ms == pytest.approx(80.0)
    assert server_clock.last_sync_at == pytest.approx(1040.0)


async def test_now_ms_uses_offset() -> None:
    clock_calls = [1000.0, 1040.0, 5000.0]
    clock = _FakeClock(clock_calls)
    server_clock = ServerClock(max_skew_ms=1000, clock=clock)

    async def fetch_server_ms() -> int:
        return 1100

    await server_clock.sync(fetch_server_ms)
    assert server_clock.now_ms() == round(5000.0 + 80.0)


async def test_skew_exceeding_max_raises_clock_skew_before_signing() -> None:
    """max_skew_ms 초과 시 서명 전 차단 — negative test."""
    clock = _FakeClock([0.0, 0.0])  # round_trip=0이므로 offset == server-local 차이
    server_clock = ServerClock(max_skew_ms=1000, clock=clock)

    async def fetch_server_ms() -> int:
        return 5000  # local=0이므로 offset=5000ms, max_skew_ms=1000 초과

    with pytest.raises(ExchangeError) as exc_info:
        await server_clock.sync(fetch_server_ms)
    assert exc_info.value.kind == ExchangeErrorKind.CLOCK_SKEW
    assert exc_info.value.retryable is False
    # 오프셋은 raise 전에 이미 갱신돼 있어야 진단에 쓸 수 있다.
    assert server_clock.offset_ms == pytest.approx(5000.0)


async def test_skew_within_bound_does_not_raise() -> None:
    clock = _FakeClock([0.0, 0.0])
    server_clock = ServerClock(max_skew_ms=1000, clock=clock)

    async def fetch_server_ms() -> int:
        return 500

    await server_clock.sync(fetch_server_ms)
    assert server_clock.offset_ms == pytest.approx(500.0)


async def test_sync_and_now_ms_latency_budget() -> None:
    """`sync()`/`now_ms()`는 요청 서명 경로마다 호출되는 hot path — 로컬
    회귀 예산이며 SLO 단언은 아니다(headless worker 지침)."""
    clock = _FakeClock([float(i) for i in range(40_000)])
    server_clock = ServerClock(max_skew_ms=1_000_000, clock=clock)

    async def fetch_server_ms() -> int:
        return 0

    await server_clock.sync(fetch_server_ms)  # 콜드 스타트 워밍업 — 예산 밖.
    server_clock.now_ms()
    started = perf_counter()
    for _ in range(9_999):
        await server_clock.sync(fetch_server_ms)
        server_clock.now_ms()
    elapsed = perf_counter() - started
    assert elapsed < 3.0, f"9999 sync+now_ms cycles took {elapsed:.3f}s (budget 3.0s)"


def test_pytest_gate_turns_red_when_skew_check_is_removed(tmp_path: Path) -> None:
    """실제 skew 초과 차단 테스트가 통과하는 걸 먼저 확인하고, skew 검사를
    무력화하면(=서명 전 차단 없이 오프셋만 조용히 갱신) pytest가 exit 1로
    red가 되는 것까지 증명한다(gate/CI red-line regression proof,
    DEPTH_L4_BR task-456 D2 미달 사유 해소)."""
    test_copy = tmp_path / "test_clock_sync_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\nasyncio_mode = auto\n", encoding="utf-8")
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-c", str(config), "--confcutdir", str(tmp_path),
        f"{test_copy}::test_skew_exceeding_max_raises_clock_skew_before_signing",
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
        "name = 'src.exchanges.common.clock_sync'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = '            if abs(self._offset_ms) > self._max_skew_ms:'\n"
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


async def test_two_independent_server_clocks_do_not_share_offset() -> None:
    """다중 인스턴스 증명(D3) — 거래소별로 만들어지는 `ServerClock` 두 개는
    완전히 독립적이어야 한다: 한쪽의 스큐가 커도 다른 쪽 오프셋에 영향이
    없다."""
    bitget_clock = _FakeClock([0.0, 0.0])
    bitget = ServerClock(max_skew_ms=1000, clock=bitget_clock)
    binance_clock = _FakeClock([0.0, 0.0])
    binance = ServerClock(max_skew_ms=1000, clock=binance_clock)

    async def fetch_500() -> int:
        return 500

    async def fetch_5000() -> int:
        return 5000

    await bitget.sync(fetch_500)
    with pytest.raises(ExchangeError):
        await binance.sync(fetch_5000)

    assert bitget.offset_ms == pytest.approx(500.0)
    assert binance.offset_ms == pytest.approx(5000.0)  # 서로 다른 값, 뒤섞이지 않음


async def test_concurrent_sync_calls_are_serialized_not_interleaved() -> None:
    """리플레이/적대적 증명(D3) — 겹쳐 들어온 두 `sync()` 호출이 인터리빙되면,
    나중에 시작했지만 먼저 응답이 도착한 쪽이 오프셋을 계산하는 사이에 다른
    호출의 `t0`/`t1`/응답이 뒤섞일 위험이 있다(리플레이 순서 공격과 동일한
    피해 형태 — 오래된 응답이 최신 상태를 덮어씀). `_lock`이 한 번에 하나의
    왕복만 완전히 끝내도록 강제하는지, 두 호출 각각의 fetch 콜백 내부
    start/end 마커가 절대 섞이지 않는 것으로 증명한다."""
    calls: list[str] = []
    clock_values = iter([0.0, 10.0, 20.0, 30.0])

    def clock() -> float:
        return next(clock_values)

    server_clock = ServerClock(max_skew_ms=1_000_000, clock=clock)

    async def fetch_a() -> int:
        calls.append("a-start")
        await asyncio.sleep(0)  # 실제 네트워크 왕복을 흉내낸 인터리빙 지점.
        calls.append("a-end")
        return 1000

    async def fetch_b() -> int:
        calls.append("b-start")
        await asyncio.sleep(0)
        calls.append("b-end")
        return 2000

    await asyncio.gather(server_clock.sync(fetch_a), server_clock.sync(fetch_b))

    assert calls in (
        ["a-start", "a-end", "b-start", "b-end"],
        ["b-start", "b-end", "a-start", "a-end"],
    )
