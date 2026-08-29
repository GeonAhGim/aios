"""9.7/9.8 — Watchdog 오탐 검증 시뮬레이터 실행.

06_mvp_scope_v1.3.md#§6.3 Definition of Done: "정책문서 8.6-A-1-1 Watchdog
오탐 시뮬레이터 최초 1회 실행(수치 통과 여부와 무관하게 실행 자체가
SCAFFOLD 완료 조건)". 이 테스트가 그 최초 실행이다 — 실제 측정치를
`print(report.summary())`로 남겨(pytest -s로 보이는 결과, CI 로그에도
남음) "실행됐다"는 사실 자체를 증거로 남긴다."""
from src.core.safety.watchdog import WatchdogAction
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
