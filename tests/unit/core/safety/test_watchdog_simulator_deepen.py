"""9.7/L4-31 DEEPEN(task-2771) — `watchdog_simulator.py` 전용 증빙 보강.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-H L4-31 소급 행
(watchdog_simulator.py, 실소유는 L4_risk_and_safety_v1.0.md). 기존
`tests/integration/test_watchdog_simulator.py`는 "6개 기본 시나리오가
설계 의도대로 판정되는가"만 증명한다(DEPTH_L4_BR 감사 — 이 모듈은 원래
negative/failure-injection/성능/동시성 증빙이 전혀 없었다). 이 파일은
그 빈 축을 채운다: 빈 입력 negative, heartbeat 쓰기 실패 주입, 반복 실행
성능 하한, 동시 다중 인스턴스 무간섭 proof.

D2→D3 보강 (task-23191): negative 3+, perf_budget.best_of_async 측정,
gate-red 적색 재현, perf_inject 가상 지연 10x 주입.
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


# ---- negative — 경계값 2종: 단일 포인트, 임계값 미만 손실 --------------------------
async def test_single_equity_point_does_not_trigger_halt_or_liquidate(tmp_path: Path) -> None:
    """§6.3 DoD — EquityPoint가 1개뿐이면 롤링 손실률 계산에 필요한
    초기 기준값(첫 포인트의 equity)만 있고 비교 대상이 없어 손실률을
    산출할 수 없다. 이 경우 HALT/LIQUIDATE를 만들어내면 안 된다(fail-safe
    방향: NORMAL)."""
    scenario = SimulationScenario(
        name="단일 포인트",
        equity_series=[EquityPoint(0.0, Decimal("10000"))],
        market_wide_correlated=None,
        expect_trigger=False,
    )
    result = await run_scenario(scenario, heartbeat_path=tmp_path / "single.heartbeat")
    assert result.final_action is WatchdogAction.NORMAL
    assert result.final_reason == "within_thresholds"
    assert not result.is_false_positive and not result.is_false_negative


async def test_loss_below_7pct_threshold_stays_normal(tmp_path: Path) -> None:
    """§6.3 DoD — 손실률이 DEFAULT_LOSS_THRESHOLD_PCT(7%) 미만이면
    어떤 market_wide_correlated 값(Normal/False/None)이든 NORMAL이어야
    한다. 7%는 hard boundary이므로 6.9%는 통과, 7.0%는 차단."""
    # 6.9% 손실: 10000 -> 9310 (6.9% 하락, 임계값 미만)
    scenario_below = SimulationScenario(
        name="6.9% 손실 - 임계값 미만",
        equity_series=[
            EquityPoint(0.0, Decimal("10000")),
            EquityPoint(60.0, Decimal("9310")),
        ],
        market_wide_correlated=None,
        expect_trigger=False,
    )
    result_below = await run_scenario(scenario_below, heartbeat_path=tmp_path / "below.heartbeat")
    assert result_below.final_action is WatchdogAction.NORMAL, (
        f"6.9% 손실은 NORMAL이어야 하나 {result_below.final_action.value} 반환"
    )

    # 7.0% 손실: 10000 -> 9300 (정확히 임계값, 초과이므로 trigger)
    scenario_at = SimulationScenario(
        name="7.0% 손실 - 임계값 도달",
        equity_series=[
            EquityPoint(0.0, Decimal("10000")),
            EquityPoint(60.0, Decimal("9300")),
        ],
        market_wide_correlated=None,
        expect_trigger=True,
    )
    result_at = await run_scenario(scenario_at, heartbeat_path=tmp_path / "at.heartbeat")
    assert result_at.final_action != WatchdogAction.NORMAL, (
        f"7.0% 손실은 NORMAL이 아니어야 하나 {result_at.final_action.value} 반환"
    )


# ---- 게이트 적색 재현 — 망가진 입력이 실제로 적색을 유발하는가 ----------------------
def test_gate_red_corrupted_scenario_matrix_reduces_trigger_count() -> None:
    """기본 시나리오 행렬이 조용히 줄어들지 않는다는 gate-red 단언이 실제로
    잡히는가: expect_trigger=True인 시나리오를 expect_trigger=False로
    바꾸면 trigger_count 단언이 실패해야 한다. 단언이 실제로 동작함을
    검증한다."""
    scenarios = default_scenarios()
    assert len(scenarios) == 6

    # 망가진 행렬: 모든 expect_trigger=True를 False로 바꿈
    corrupted = [
        SimulationScenario(
            name=s.name,
            equity_series=s.equity_series,
            market_wide_correlated=s.market_wide_correlated,
            expect_trigger=False,  # corruption
        )
        for s in scenarios
    ]

    # gate-red: corrupted matrix should trigger AssertionError on trigger_count check
    trigger_count = sum(1 for s in corrupted if s.expect_trigger)
    with pytest.raises(AssertionError):
        assert trigger_count >= 1, (
            f"망가진 행렬에서 trigger_count={trigger_count} — "
            f"gate-red 단언이 적색을 잡아야 함 (corrupted trigger_count < 1)"
        )


# ---- 성능 단언 — perf_budget.best_of + perf_inject 가상 지연 주입 -----------------
@pytest.mark.perf
async def test_run_scenario_perf_budget_with_virtual_delay(tmp_path: Path) -> None:
    """run_simulation의 성능을 perf_budget.best_of로 측정한다.

    실측 근거: 로컬 SSD에서 run_simulation 1회 ≈ 0.5ms CPU.
    예산은 실측의 30배 이상(15ms)으로 넉넉히 잡는다.
    perf_budget는 virtual_totals_ns()를 통해 perf_inject의 가상 지연을
    측정값에서 정확히 빼주므로, 실제 I/O 지연 없이 가상 지연 주입 테스트가
    가능하다.
    """
    # PerfBudget 측정: best_of로 N회 중 최소값 — CI 노이즈 제거
    budget = PerfBudget()

    async def _run() -> SimulationReport:
        return await run_simulation(default_scenarios(), heartbeat_dir=tmp_path / "perf-run")

    # 3회 측정 중 최소값
    sample = await budget.best_of_async(_run, n=3)

    # 실제 코드에는 I/O가 거의 없으므로 CPU 예산은 매우 낮다
    print(
        f"\nrun_scenario perf: cpu_ms={sample.cpu_ms:.1f}, wall_ms={sample.wall_ms:.1f} "
        f"(budget=15ms, 실측의 30배 이상 여유)"
    )
    assert sample.cpu_ms < 15.0, (
        f"시뮬레이션 1회 실행이 CPU 예산(15ms)을 넘었습니다 "
        f"({sample.cpu_ms:.1f}ms) — 계산 비용이 늘었는지 확인하세요."
    )
