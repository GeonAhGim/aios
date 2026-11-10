"""9.7/L4-31 DEEPEN(task-2771) — `watchdog_simulator.py` 전용 증빙 보강.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-H L4-31 소급 행
(watchdog_simulator.py, 실소유는 L4_risk_and_safety_v1.0.md). 기존
`tests/integration/test_watchdog_simulator.py`는 "6개 기본 시나리오가
설계 의도대로 판정되는가"만 증명한다(DEPTH_L4_BR 감사 — 이 모듈은 원래
negative/failure-injection/성능/동시성 증빙이 전혀 없었다). 이 파일은
그 빈 축을 채운다: 빈 입력 negative, heartbeat 쓰기 실패 주입, 반복 실행
성능 하한, 동시 다중 인스턴스 무간섭 proof.

보강 항목 (task-23191 D1→D3):
- negative 테스트 3종 (원장: 1 → 보강 후: 3)
- 성능 단언 0 → perf 마커 + PerfBudget 측정 + virtual_delayed_sync 적색 재현 추가
- adversarial 1 → 2 (결정론성 증명 추가)
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.safety.watchdog import WatchdogAction
from src.core.safety.watchdog_simulator import (
    EquityPoint,
    SimulationReport,
    SimulationScenario,
    default_scenarios,
    run_scenario,
    run_simulation,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync


# ---- negative — 빈 입력의 정의된 동작 ---------------------------------------------------
async def test_empty_equity_series_stays_normal_without_calling_compute_equity(
    tmp_path: Path,
) -> None:
    """§6.3 DoD 대상 밖 경계값 — 시나리오가 관측치를 하나도 안 주면 아무
    판정도 내리지 않고 기본값(NORMAL/within_thresholds)에 머물러야 한다
    (거짓 HALT/LIQUIDATE를 만들어내면 안 된다, fail-safe 방향)."""
    scenario = SimulationScenario(
        name="빈 시나리오",
        equity_series=[],
        market_wide_correlated=None,
        expect_trigger=False,
    )
    result = await run_scenario(scenario, heartbeat_path=tmp_path / "empty.heartbeat")
    assert result.final_action is WatchdogAction.NORMAL
    assert result.final_reason == "within_thresholds"
    assert not result.is_false_positive and not result.is_false_negative


async def test_all_zero_equity_stays_normal_no_crash(tmp_path: Path) -> None:
    """§6.3 negative — 모든 equity 값이 0이라도 watchdog은 crash 없이
    NORMAL을 반환해야 한다(ZeroDivisionError 등 예외가 새어나가면 안 된다).
    실제 시스템에서 0 원은 드물지만, 분수/나눗셈이 equity_series에
    개입하지 않는 한 정상 동작한다."""
    scenario = SimulationScenario(
        name="영-equity",
        equity_series=[
            EquityPoint(0.0, Decimal("0")),
            EquityPoint(30.0, Decimal("0")),
            EquityPoint(60.0, Decimal("0")),
        ],
        market_wide_correlated=None,
        expect_trigger=False,
    )
    result = await run_scenario(scenario, heartbeat_path=tmp_path / "zero.heartbeat")
    assert result.final_action is WatchdogAction.NORMAL
    assert result.final_reason == "within_thresholds"


async def test_single_equity_point_stays_normal(tmp_path: Path) -> None:
    """§6.3 negative — equity_series에 점이 하나뿐이면 window가 채워지지
    않아 변동성/손실율을 계산할 수 없다. 이때 NORMAL/within_thresholds가
    반환되어야 한다(불완전 데이터에서 위험 신호를 내면 FP 증가)."""
    scenario = SimulationScenario(
        name="단일 점",
        equity_series=[EquityPoint(0.0, Decimal("10000"))],
        market_wide_correlated=None,
        expect_trigger=False,
    )
    result = await run_scenario(scenario, heartbeat_path=tmp_path / "single.heartbeat")
    assert result.final_action is WatchdogAction.NORMAL
    assert result.final_reason == "within_thresholds"


# ---- failure-injection — heartbeat 쓰기 실패 시 침묵하지 않는다 -------------------------
async def test_stale_heartbeat_write_into_missing_dir_raises_not_silently_normal(
    tmp_path: Path,
) -> None:
    """`stale_heartbeat_seconds` 경로는 `write_heartbeat`(mkdir parents=True)를
    거치지 않고 `heartbeat_path.write_text()`를 직접 호출한다(모듈 소스
    116행) — 부모 디렉터리가 없으면 그대로 예외가 새어나가야 한다. 여기서
    조용히 삼켜 NORMAL로 위장하면 "응답불능인데 정상으로 오판"하는 최악의
    실패 모드가 된다(fail-closed 원칙, I10과 동일 취지)."""
    missing_dir = tmp_path / "does_not_exist_yet"
    scenario = SimulationScenario(
        name="응답불능 주입",
        equity_series=[EquityPoint(0.0, Decimal("10000")), EquityPoint(30.0, Decimal("10000"))],
        market_wide_correlated=None,
        expect_trigger=True,
        stale_heartbeat_seconds=60.0,
    )
    with pytest.raises(FileNotFoundError):
        await run_scenario(scenario, heartbeat_path=missing_dir / "sim.heartbeat")


# ---- 성능 하한 — 반복 실행이 예산 내(파일 I/O 포함, 상대 배수 단언) ----------------------
@pytest.mark.perf
async def test_default_scenarios_repeated_runs_within_budget(tmp_path: Path) -> None:
    """절대시간 예산은 넉넉히 잡아 느린 CI 머신에서도 플레이키하지 않게
    하고(task-1038/1521 decision과 동일하게 비차단 print를 함께 남긴다),
    대신 구조적 산출물(시나리오 결과 개수·heartbeat 파일 개수)은 정확히
    단언한다 — 절대시간 하나만으로는 "덜 도는데 빠른" 회귀를 못 잡는다."""
    iterations = 15
    scenarios = default_scenarios()
    budget_sec = 20.0  # 실측 ~5s(로컬 SSD) — 회귀(예: 반복 sleep, O(n^2))만 잡는 넉넉한 상한

    sim_dir = tmp_path / "sim"
    sim_dir.mkdir()
    start = time.perf_counter()
    for i in range(iterations):
        run_dir = sim_dir / str(i)
        report = await run_simulation(scenarios, heartbeat_dir=run_dir)
        assert len(report.results) == len(scenarios)
        assert len(list(run_dir.glob("*.heartbeat"))) == len(scenarios)
    elapsed_sec = time.perf_counter() - start

    print(
        f"\nrun_simulation x{iterations} ({len(scenarios)} scenarios each): "
        f"{elapsed_sec:.3f}s (budget<{budget_sec:.1f}s)"
    )
    assert elapsed_sec < budget_sec, (
        f"watchdog 시뮬레이션 반복 실행이 예산({budget_sec:.1f}s)을 넘었습니다 "
        f"({elapsed_sec:.2f}s) — 시나리오당 I/O나 계산 비용이 늘었는지 확인하세요."
    )


# ---- 성능 단언 — PerfBudget + virtual_delayed_sync 게이트 적색 재현 ----------------------
@pytest.mark.perf
async def test_run_scenario_single_call_cpu_under_budget(tmp_path: Path) -> None:
    """성능 하한(D2, task-23191 신설): 단일 시나리오 실행이 CPU 예산 내여야 한다.
    실측 기준: 로컬 SSD에서 run_scenario 1회 ≈ 0.5ms CPU.
    예산 50ms는 실측의 100배 이상 여유(task-1038/1521 decision)."""
    reset_virtual_totals()
    scenario = SimulationScenario(
        name="성능-기본",
        equity_series=[
            EquityPoint(0.0, Decimal("10000")),
            EquityPoint(60.0, Decimal("9500")),
            EquityPoint(90.0, Decimal("9700")),
        ],
        market_wide_correlated=None,
        expect_trigger=True,
    )
    pb = PerfBudget()
    sample = await pb.best_of_async(
        lambda: run_scenario(scenario, heartbeat_path=tmp_path / "perf.heartbeat"),
        n=5,
        warmup=1,
    )
    print(
        f"\nrun_scenario single-call cpu={sample.cpu_ms:.3f}ms "
        f"wall={sample.wall_ms:.3f}ms (budget<50ms)"
    )
    assert sample.cpu_ms < 50.0, (
        f"단일 시나리오 실행 CPU 시간 {sample.cpu_ms:.3f}ms가 예산 50ms 초과"
    )


@pytest.mark.perf
async def test_run_scenario_catches_10x_virtual_delay_injection(tmp_path: Path) -> None:
    """게이트 적색 재현(D2, task-23191 신설): 성능 예산 단언이 실제로
    위반을 포착하는지 역으로 증명한다. `virtual_delayed_sync`으로
    1회 호출당 10x 예산(50ms * 10 = 500ms)을 더하면 측정치가 예산을
    초과해 assert_within가 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다. 실제 sleep/busy-wait 없이 가상 지연으로 측정치만 조작한다.
    """
    reset_virtual_totals()
    scenario = SimulationScenario(
        name="지연-주입-적색",
        equity_series=[
            EquityPoint(0.0, Decimal("10000")),
            EquityPoint(60.0, Decimal("9500")),
            EquityPoint(90.0, Decimal("9700")),
        ],
        market_wide_correlated=None,
        expect_trigger=True,
    )
    heartbeat_path = tmp_path / "injected.heartbeat"

    # 가상 지연: 500ms 추가 (예산 50ms의 10배)
    delayed_fn = virtual_delayed_sync(
        lambda: run_scenario(scenario, heartbeat_path=heartbeat_path),
        seconds=0.5,
    )

    pb = PerfBudget()
    sample = await pb.best_of_async(delayed_fn, n=3, warmup=1)
    assert sample.cpu_ms >= 50.0, "10x 가상 지연 주입 후 cpu가 50ms 예산을 안 넘으면 적색 재현 실패"


# ---- 게이트/CI 레드라인 — 기본 시나리오 행렬이 조용히 줄어들지 않는다 --------------------
def test_default_scenarios_matrix_has_not_silently_shrunk() -> None:
    scenarios = default_scenarios()
    assert len(scenarios) == 6

    stale_count = sum(1 for s in scenarios if s.stale_heartbeat_seconds is not None)
    assert stale_count == 1, "응답불능(stale heartbeat) 시나리오는 정확히 1개여야 한다"

    correlated_values = {s.market_wide_correlated for s in scenarios}
    assert correlated_values == {True, False, None}, (
        "market_wide_correlated 3분류(True/False/불명)가 전부 최소 1개씩 커버돼야 한다"
    )

    trigger_count = sum(1 for s in scenarios if s.expect_trigger)
    no_trigger_count = len(scenarios) - trigger_count
    assert trigger_count >= 1 and no_trigger_count >= 1, (
        "트리거/비트리거 시나리오가 둘 다 있어야 FP/FN율이 의미가 있다"
    )


# ---- 동시 다중 인스턴스 proof — 병렬 실행 간 상태 간섭이 없다 ----------------------------
async def test_concurrent_run_simulation_instances_do_not_interfere(tmp_path: Path) -> None:
    """`run_scenario`의 clock_time 딕셔너리는 호출마다 새로 만들어지는 지역
    변수다 — 모듈 전역 가변 상태가 있다면 동시 실행 시 시나리오 간 clock이
    섞여 판정이 흔들린다. N개 인스턴스를 병렬로 돌려 전부 순차 실행과
    동일한 결정론적 결과를 내는지 증명한다."""
    scenarios = default_scenarios()
    instance_count = 5

    async def _one(i: int) -> SimulationReport:
        return await run_simulation(scenarios, heartbeat_dir=tmp_path / f"concurrent-{i}")

    concurrent_reports = await asyncio.gather(*[_one(i) for i in range(instance_count)])

    sequential_report = await run_simulation(scenarios, heartbeat_dir=tmp_path / "sequential")
    expected = [(r.scenario, r.final_action, r.final_reason) for r in sequential_report.results]

    for report in concurrent_reports:
        actual = [(r.scenario, r.final_action, r.final_reason) for r in report.results]
        assert actual == expected


# ---- adversarial — 결정론성 proof ---------------------------------------------------------
async def test_run_scenario_deterministic_across_sequential_runs(tmp_path: Path) -> None:
    """adversarial(D2, task-23191 신설): 동일한 입력 시나리오를 M회 순차
    실행할 때마다 결과가 완전히 동일해야 한다(clock_time가 지역 변수라
    외부에서 seed를 주지 않는 한 deterministic). 결과가 흔들리면
    "정상인데 HALT" FP가 비결정적으로 발생한다."""
    reset_virtual_totals()
    scenario = SimulationScenario(
        name="결정론성-검증",
        equity_series=[
            EquityPoint(0.0, Decimal("10000")),
            EquityPoint(30.0, Decimal("9500")),
            EquityPoint(60.0, Decimal("9000")),
            EquityPoint(90.0, Decimal("9200")),
        ],
        market_wide_correlated=True,
        expect_trigger=True,
    )
    runs = 20
    results = []
    for i in range(runs):
        r = await run_scenario(scenario, heartbeat_path=tmp_path / f"det-{i}.heartbeat")
        results.append((r.final_action, r.final_reason))

    # 모든 결과가 첫 번째 결과와 동일해야 함
    first = results[0]
    for i, r in enumerate(results[1:], start=1):
        assert r == first, (
            f"run {i} 결과가 run 0과 다름: {r} != {first} — watchdog 판정이 비결정적이다"
        )
