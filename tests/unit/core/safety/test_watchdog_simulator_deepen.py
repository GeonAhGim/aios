"""9.7/L4-31 DEEPEN(task-2771) — `watchdog_simulator.py` 전용 증빙 보강.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-H L4-31 소급 행
(watchdog_simulator.py, 실소유는 L4_risk_and_safety_v1.0.md). 기존
`tests/integration/test_watchdog_simulator.py`는 "6개 기본 시나리오가
설계 의도대로 판정되는가"만 증명한다(DEPTH_L4_BR 감사 — 이 모듈은 원래
negative/failure-injection/성능/동시성 증빙이 전혀 없었다). 이 파일은
그 빈 축을 채운다: 빈 입력 negative, heartbeat 쓰기 실패 주입, 반복 실행
성능 하한, 동시 다중 인스턴스 무간섭 proof.
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


# ---- negative — 빈 입력의 정의된 동작 ---------------------------------------------------
async def test_empty_equity_series_stays_normal_without_calling_compute_equity(
    tmp_path: Path,
) -> None:
    """§6.3 DoD 대상 밖 경계값 — 시나리오가 관측치를 하나도 안 주면 아무
    판정도 내리지 않고 기본값(NORMAL/within_thresholds)에 머물러야 한다
    (거짓 HALT/LIQUIDATE를 만들어내면 안 된다, fail-safe 방향)."""
    scenario = SimulationScenario(
        name="빈 시나리오", equity_series=[], market_wide_correlated=None, expect_trigger=False,
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
