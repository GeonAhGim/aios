"""9.7 — Watchdog 오탐 검증 시뮬레이터.

Spec: 정책문서 8.6-A-1-1, 06_mvp_scope_v1.3.md#§6.3 Definition of Done,
10_implementation_task_tree_v1.9.md 9.7/9.8

과거 Flash Crash 데이터를 재생해 FD-9.2 판정 로직(decide())의 오탐(정상인데
HALT/LIQUIDATE) / 누락(실제 위험인데 NORMAL)률을 측정한다. §6.3 DoD의
완료조건은 "1회 실행 자체"이지 수치 통과가 아니다(수치 통과는 FROZEN
착수 조건, 20.1-A A그룹) — 이 도구는 측정한 실제 수치를 있는 그대로
보고한다.

편차 — 실제 과거 시세 재생에는 FD-2(시장데이터 파이프라인) 히스토리
저장소가 필요한데 이 세션 스콥엔 없다. 대신 잘 알려진 크립토 Flash
Crash 패턴(급락→일부 반등의 V자형)과 정상 변동성 구간을 합성 데이터로
재현한다 — 실제 히스토리 데이터가 생기면 이 파일의 시나리오 목록만
과거 시세로 교체하면 된다(SimulationScenario 인터페이스는 그대로).

_EquityWindow에 주입한 가짜 clock 덕분에 5분 롤링 윈도우를 실시간 대기
없이 즉시 재생한다 — 응답불능(unresponsive_sec) 시나리오만은 실제
heartbeat 파일의 벽시계 타임스탬프를 직접 과거로 써서 재현한다
(read_heartbeat_age_seconds가 time.time() 기준이라 이쪽은 clock 주입
대상이 아님, watchdog_process.py 통합테스트와 동일 기법).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.watchdog import WatchdogAction, WatchdogService, decide


@dataclass
class EquityPoint:
    seconds_from_start: float
    equity: Decimal


@dataclass
class SimulationScenario:
    name: str
    equity_series: list[EquityPoint]
    market_wide_correlated: bool | None
    expect_trigger: bool  # HALT 또는 LIQUIDATE 중 아무거나라도 기대되면 True
    stale_heartbeat_seconds: float | None = None  # 응답불능 시나리오 전용


@dataclass
class ScenarioResult:
    scenario: str
    final_action: WatchdogAction
    final_reason: str
    expect_trigger: bool

    @property
    def is_false_positive(self) -> bool:
        return self.final_action != WatchdogAction.NORMAL and not self.expect_trigger

    @property
    def is_false_negative(self) -> bool:
        return self.final_action == WatchdogAction.NORMAL and self.expect_trigger


@dataclass
class SimulationReport:
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def false_positive_rate(self) -> float:
        negatives = [r for r in self.results if not r.expect_trigger]
        if not negatives:
            return 0.0
        return sum(1 for r in negatives if r.is_false_positive) / len(negatives)

    @property
    def false_negative_rate(self) -> float:
        positives = [r for r in self.results if r.expect_trigger]
        if not positives:
            return 0.0
        return sum(1 for r in positives if r.is_false_negative) / len(positives)

    def summary(self) -> str:
        lines = [
            f"{'시나리오':<28} {'기대':<8} {'실제':<10} {'사유'}",
        ]
        for r in self.results:
            flag = " ← FP" if r.is_false_positive else (" ← FN" if r.is_false_negative else "")
            expected = "발동" if r.expect_trigger else "정상"
            lines.append(
                f"{r.scenario:<28} {expected:<8} {r.final_action.value:<10} "
                f"{r.final_reason}{flag}"
            )
        lines.append("")
        lines.append(
            f"False Positive rate: {self.false_positive_rate:.1%} (목표 <1%) | "
            f"False Negative rate: {self.false_negative_rate:.1%} (목표 =0%)"
        )
        return "\n".join(lines)


async def run_scenario(scenario: SimulationScenario, *, heartbeat_path: Path) -> ScenarioResult:
    clock_time = {"t": 0.0}

    def clock() -> float:
        return clock_time["t"]

    if scenario.stale_heartbeat_seconds is not None:
        heartbeat_path.write_text(str(time.time() - scenario.stale_heartbeat_seconds))
    else:
        write_heartbeat(heartbeat_path)

    points = iter(scenario.equity_series)

    async def compute_equity() -> Decimal:
        point = next(points)
        clock_time["t"] = point.seconds_from_start
        return point.equity

    async def health_check() -> bool:
        return True

    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=health_check,
        heartbeat_path=heartbeat_path,
        clock=clock,
    )

    final_action = WatchdogAction.NORMAL
    final_reason = "within_thresholds"
    for _ in scenario.equity_series:
        snapshot = await service.take_snapshot()
        decision = decide(snapshot, market_wide_correlated=scenario.market_wide_correlated)
        final_action, final_reason = decision.action, decision.reason

    return ScenarioResult(
        scenario=scenario.name,
        final_action=final_action,
        final_reason=final_reason,
        expect_trigger=scenario.expect_trigger,
    )


async def run_simulation(
    scenarios: list[SimulationScenario], *, heartbeat_dir: Path
) -> SimulationReport:
    report = SimulationReport()
    for i, scenario in enumerate(scenarios):
        heartbeat_path = heartbeat_dir / f"sim-{i}.heartbeat"
        report.results.append(await run_scenario(scenario, heartbeat_path=heartbeat_path))
    return report


def _flat(equity: str, count: int, *, step_seconds: float = 30.0) -> list[EquityPoint]:
    return [
        EquityPoint(seconds_from_start=i * step_seconds, equity=Decimal(equity))
        for i in range(count)
    ]


def _v_shaped_crash(
    peak: str, trough: str, recovery: str, *, drop_at_seconds: float = 60.0
) -> list[EquityPoint]:
    """peak 유지 → drop_at_seconds 시점에 trough로 급락 → recovery로 일부 반등.
    실제 Flash Crash의 전형적 형태(급락 후 부분 회복)를 최소 3점으로 압축."""
    return [
        EquityPoint(seconds_from_start=0.0, equity=Decimal(peak)),
        EquityPoint(seconds_from_start=drop_at_seconds, equity=Decimal(trough)),
        EquityPoint(seconds_from_start=drop_at_seconds + 30.0, equity=Decimal(recovery)),
    ]


def default_scenarios() -> list[SimulationScenario]:
    """9.8 통합테스트가 재생할 기본 시나리오 — 실제 히스토리 데이터가
    생기기 전까지의 Draft 대체 세트(모듈 docstring 편차 설명 참조)."""
    return [
        SimulationScenario(
            name="Flash Crash - 시장 전체 급변(BTC -15%)",
            equity_series=_v_shaped_crash("10000", "8500", "9000"),
            market_wide_correlated=True,
            expect_trigger=True,
        ),
        SimulationScenario(
            name="고립된 급락 - 조작 의심(단일 계좌만 -15%)",
            equity_series=_v_shaped_crash("10000", "8500", "9000"),
            market_wide_correlated=False,
            expect_trigger=True,
        ),
        SimulationScenario(
            name="상관성 판정 불가(FD-2.6 데이터 부족, -15%)",
            equity_series=_v_shaped_crash("10000", "8500", "9000"),
            market_wide_correlated=None,
            expect_trigger=True,
        ),
        SimulationScenario(
            name="정상 변동성(±2% 등락)",
            equity_series=[
                EquityPoint(0.0, Decimal("10000")),
                EquityPoint(30.0, Decimal("9850")),
                EquityPoint(60.0, Decimal("10100")),
                EquityPoint(90.0, Decimal("9900")),
            ],
            market_wide_correlated=None,
            expect_trigger=False,
        ),
        SimulationScenario(
            name="완만한 하락(-5%, 임계값 7% 미만)",
            equity_series=[
                EquityPoint(0.0, Decimal("10000")),
                EquityPoint(60.0, Decimal("9700")),
                EquityPoint(120.0, Decimal("9500")),
            ],
            market_wide_correlated=None,
            expect_trigger=False,
        ),
        SimulationScenario(
            name="메인 프로세스 응답불능(60초)",
            equity_series=_flat("10000", 2),
            market_wide_correlated=None,
            expect_trigger=True,
            stale_heartbeat_seconds=60.0,
        ),
    ]
