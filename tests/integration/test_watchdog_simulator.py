"""9.7/9.8 — Watchdog 오탐 검증 시뮬레이터 실행.

06_mvp_scope_v1.3.md#§6.3 Definition of Done: "정책문서 8.6-A-1-1 Watchdog
오탐 시뮬레이터 최초 1회 실행(수치 통과 여부와 무관하게 실행 자체가
SCAFFOLD 완료 조건)". 이 테스트가 그 최초 실행이다 — 실제 측정치를
`print(report.summary())`로 남겨(pytest -s로 보이는 결과, CI 로그에도
남음) "실행됐다"는 사실 자체를 증거로 남긴다."""

from decimal import Decimal

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.split_brain import Diagnosis, FailureDomain
from src.core.safety.watchdog import (
    DEFAULT_LOSS_THRESHOLD_PCT,
    WatchdogAction,
    WatchdogService,
    WatchdogSnapshot,
    decide,
)
from src.core.safety.watchdog_simulator import default_scenarios, run_simulation


async def test_watchdog_simulator_runs_once_and_measures_fp_fn_rate(tmp_path):
    scenarios = default_scenarios()

    report = await run_simulation(scenarios, heartbeat_dir=tmp_path)

    print("\n" + report.summary())  # noqa: T201 — DoD가 요구하는 "측정 자체"의 가시적 증거

    assert len(report.results) == len(scenarios)
    # Draft 시나리오 세트 자체의 정확성 검증 — 각 시나리오가 설계 의도대로
    # 판정되는지 개별 확인(집계 비율만 보면 우연히 상쇄될 수 있음).
    by_name = {r.scenario: r for r in report.results}
    assert by_name["Flash Crash - 시장 전체 급변(BTC -15%)"].final_action == (
        WatchdogAction.LIQUIDATE
    )
    assert by_name["고립된 급락 - 조작 의심(단일 계좌만 -15%)"].final_action == (
        WatchdogAction.HALT
    )
    assert by_name["상관성 판정 불가(FD-2.6 데이터 부족, -15%)"].final_action == (
        WatchdogAction.HALT
    )
    assert by_name["정상 변동성(±2% 등락)"].final_action == WatchdogAction.NORMAL
    assert by_name["완만한 하락(-5%, 임계값 7% 미만)"].final_action == WatchdogAction.NORMAL
    assert by_name["메인 프로세스 응답불능(60초)"].final_action == WatchdogAction.HALT

    assert report.false_positive_rate < 0.01
    assert report.false_negative_rate == 0.0


async def test_run_simulation_with_no_scenarios_reports_zero_rates(tmp_path):
    """빈 시나리오 목록은 판정할 것이 없다 — 0/0을 오탐/누락 100%로
    부풀리지 않고 0.0으로 fail-closed 하게 보고해야 한다(불변식: 근거
    없는 비율을 주장하지 않는다)."""
    report = await run_simulation([], heartbeat_dir=tmp_path)

    assert report.results == []
    assert report.false_positive_rate == 0.0
    assert report.false_negative_rate == 0.0


def test_decide_rejects_loss_at_exact_threshold_as_normal():
    """손실률이 임계값과 정확히 같은 경계값은 "아직 정상"으로 오판하면
    안 된다 — FD-9.2는 `>=` 비교이므로 7.0%는 이미 발동 대상이다."""
    snapshot = WatchdogSnapshot(
        loss_pct=DEFAULT_LOSS_THRESHOLD_PCT,
        unresponsive_sec=0.0,
        exchange_healthy=True,
    )

    decision = decide(snapshot, market_wide_correlated=True)

    assert decision.action != WatchdogAction.NORMAL
    assert decision.action == WatchdogAction.LIQUIDATE


def test_decide_does_not_downgrade_liquidate_for_unrelated_failure_domain():
    """DB_ISOLATED_FAILURE가 아닌 다른 진단(예: NORMAL)이 실려 오면
    강제청산을 HALT로 낮춰서는 안 된다 — downgrade 조건은 정확히
    DB_ISOLATED_FAILURE일 때만 적용되어야 한다(범위를 넓히면 정당한
    청산이 조용히 무력화된다)."""
    snapshot = WatchdogSnapshot(
        loss_pct=Decimal("15.0"),
        unresponsive_sec=0.0,
        exchange_healthy=True,
    )
    unrelated_domain = FailureDomain(
        db_ok=True, exchange_ok=True, main_process_ok=True, diagnosis=Diagnosis.NORMAL
    )

    decision = decide(snapshot, market_wide_correlated=True, failure_domain=unrelated_domain)

    assert decision.action == WatchdogAction.LIQUIDATE
    assert decision.reason == "market_wide_correlated_loss"


def test_decide_rejects_unresponsive_with_large_loss_as_mere_unresponsiveness():
    """응답불능 판정(main_process_unresponsive)은 손실이 임계값 미만일
    때만 적용된다 — 응답불능이면서 손실도 임계값을 넘은 경우를 단순
    "응답불능"으로만 축소 보고하면 청산/정지가 필요한 손실 신호를
    감춘다."""
    snapshot = WatchdogSnapshot(
        loss_pct=Decimal("20.0"),
        unresponsive_sec=60.0,
        exchange_healthy=True,
    )

    decision = decide(snapshot, market_wide_correlated=False)

    assert decision.reason != "main_process_unresponsive"
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "isolated_loss_suspected_manipulation"


async def test_watchdog_service_does_not_mask_prior_loss_when_equity_lookup_fails(tmp_path):
    """실패주입 — 손실을 이미 관측한 뒤 compute_equity가 예외를 던지면
    (거래소 조회 실패), 마지막으로 알려진 손실률을 0으로 리셋하지 않고
    유지한 채 exchange_healthy=False로만 표시해야 한다. docstring이
    명시한 불변식("조회 실패를 손실 없음으로 오판하지 않는다")을
    검증한다."""
    heartbeat_path = tmp_path / "svc.heartbeat"

    write_heartbeat(heartbeat_path)

    calls = {"n": 0}

    async def compute_equity() -> Decimal:
        calls["n"] += 1
        if calls["n"] == 1:
            return Decimal("8000")  # peak
        raise ConnectionError("exchange unreachable")

    async def health_check() -> bool:
        return True

    clock_time = {"t": 0.0}
    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=health_check,
        heartbeat_path=heartbeat_path,
        clock=lambda: clock_time["t"],
    )

    first = await service.take_snapshot()
    assert first.exchange_healthy is True

    clock_time["t"] = 30.0
    second = await service.take_snapshot()

    assert second.exchange_healthy is False
    assert second.loss_pct == first.loss_pct


async def test_watchdog_service_first_lookup_failure_reports_unhealthy_not_normal(tmp_path):
    """실패주입 — 첫 조회부터 실패하면(과거 스냅샷 없음) 손실률은 0으로
    남되, exchange_healthy는 반드시 False여야 한다 — 이 플래그가 없으면
    "정상(NORMAL)"과 구분이 불가능해 최초 장애가 조용히 묻힌다."""
    heartbeat_path = tmp_path / "svc-first.heartbeat"

    write_heartbeat(heartbeat_path)

    async def compute_equity() -> Decimal:
        raise TimeoutError("exchange API timeout")

    async def health_check() -> bool:
        return True

    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=health_check,
        heartbeat_path=heartbeat_path,
        clock=lambda: 0.0,
    )

    snapshot = await service.take_snapshot()

    assert snapshot.exchange_healthy is False
    assert snapshot.loss_pct == Decimal("0")
