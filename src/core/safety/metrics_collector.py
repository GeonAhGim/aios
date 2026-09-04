"""9.4 evaluate(metrics) 실배선 — CircuitBreakerMetrics 수집기.

Spec: 정책문서 8.6-B, config/risk_policy.yaml circuit_breaker 섹션.
PM 배정 ⑤(agent-platform-12, 2026-09-03) + R-43(2026-09-04).

`CircuitBreakerService.evaluate()`가 소비하는 5개 지표 중, DB만으로
정직하게 계산 가능한 2개(order_reject_rate_pct, daily_loss_pct)는 이
모듈이 직접 쿼리한다. 나머지 3개(api_error_rate_pct, api_disconnect_sec,
data_delay_sec)는 실시간 관측이 필요해 이 모듈 스스로 "만들지" 않는다 —
`ApiCallTracker`는 호출부(실제로 어댑터를 부르는 지점)가
`record_success()`/`record_failure()`로 보고한 결과를 누적할 뿐이고,
`data_delay_sec`는 R-42가 만든 `DataFreshnessTracker`(주입받는 옵션
인자)의 `max_delay_sec(now)`를 그대로 읽는다 — 트래커가 없거나 아직
관측이 0건이면 `None`을 그대로 돌려준다("모름"을 "지연 0초"로 뭉개지
않는다, fail-closed는 circuit_breaker.compute_level의 몫).
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg

from src.core.safety.circuit_breaker import CircuitBreakerMetrics
from src.core.safety.data_freshness import DataFreshnessTracker

_ORDER_REJECT_WINDOW_MINUTES = 60


class ApiCallTracker:
    """최근 N회 어댑터 호출의 성공/실패 롤링 윈도우 + 마지막 성공 이후
    경과시간. 프로세스 메모리에만 있다 — 단일 main.py 프로세스 안에서
    공유하면 충분하다(watchdog_process.py는 별도 프로세스라 이 트래커를
    공유하지 않는다, 그쪽은 자체 check_exchange()가 이미 있음)."""

    def __init__(
        self, *, window_size: int = 100, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._window: deque[bool] = deque(maxlen=window_size)
        self._clock = clock
        self._last_success_at: float | None = None

    def record_success(self) -> None:
        self._window.append(True)
        self._last_success_at = self._clock()

    def record_failure(self) -> None:
        self._window.append(False)

    def error_rate_pct(self) -> Decimal:
        if not self._window:
            return Decimal("0")
        failures = sum(1 for ok in self._window if not ok)
        return Decimal(failures) / Decimal(len(self._window)) * 100

    def seconds_since_last_success(self) -> Decimal:
        """한 번도 성공한 적 없으면(프로세스 시작 직후 등) 0 — "장애가
        N초 지속됨"을 아직 증명하지 못한 상태를 emergency로 오판하지
        않는다(판단 불가를 위험으로 취급하지 않는 쪽으로 — 다른 안전
        신호들이 이미 fail-closed이므로 이 신호까지 과민할 필요 없음)."""
        if self._last_success_at is None:
            return Decimal("0")
        return Decimal(str(self._clock() - self._last_success_at))


async def _order_reject_rate_pct(
    pool: asyncpg.Pool, *, window_minutes: int = _ORDER_REJECT_WINDOW_MINUTES
) -> Decimal:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'REJECTED') AS rejected,
                COUNT(*) AS total
            FROM orders
            WHERE created_at >= now() - ($1 || ' minutes')::interval
            """,
            str(window_minutes),
        )
    if row["total"] == 0:
        return Decimal("0")
    return Decimal(row["rejected"]) / Decimal(row["total"]) * 100


async def _daily_loss_pct(pool: asyncpg.Pool) -> Decimal:
    """RUNNING 실행들의 (오늘 시작 equity 합) 대비 (현재 근사 equity 합)
    낙폭 — watchdog_process.py::compute_system_equity와 동일한
    (allocated_capital + realized_pnl) 근사를 "현재"로 쓰고,
    equity_day_start_value 합을 "오늘 시작"으로 비교한다(둘 다 4747bb11f733
    마이그레이션으로 생긴 컬럼). 이득(음수 손실)은 0으로 클램프한다 —
    이 지표는 "손실"만 의미가 있다."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH per_execution AS (
                SELECT e.id, e.equity_day_start_value, e.allocated_capital,
                       COALESCE(SUM(p.realized_pnl), 0) AS realized_pnl
                FROM strategy_executions e
                LEFT JOIN positions p ON p.execution_id = e.id
                WHERE e.status = 'RUNNING'
                GROUP BY e.id
            )
            SELECT
                COALESCE(SUM(equity_day_start_value), 0) AS day_start_total,
                COALESCE(SUM(allocated_capital), 0) + COALESCE(SUM(realized_pnl), 0)
                    AS current_total
            FROM per_execution
            """
        )
    day_start_total: Decimal = row["day_start_total"]
    if day_start_total <= 0:
        return Decimal("0")
    current_total: Decimal = row["current_total"]
    loss_pct = (day_start_total - current_total) / day_start_total * 100
    return max(loss_pct, Decimal("0"))


async def collect_circuit_breaker_metrics(
    pool: asyncpg.Pool,
    api_tracker: ApiCallTracker,
    freshness_tracker: DataFreshnessTracker | None = None,
) -> CircuitBreakerMetrics:
    """`freshness_tracker`는 기존 호출부 무회귀를 위한 옵션 인자(기본 None) —
    아직 배선되지 않은 호출부는 `data_delay_sec=None`("모름")을 받는다. 이를
    `Decimal("0")`("지연 없음")으로 대체하지 않는다 — 두 상태는 의미가 다르고,
    None을 0으로 뭉개면 CB가 stale 데이터에서도 절대 트립하지 않는 fail-open
    결함(§9 R-43 원문)이 재발한다."""
    data_delay_sec = (
        freshness_tracker.max_delay_sec(datetime.now(timezone.utc))
        if freshness_tracker is not None
        else None
    )
    return CircuitBreakerMetrics(
        api_error_rate_pct=api_tracker.error_rate_pct(),
        api_disconnect_sec=api_tracker.seconds_since_last_success(),
        data_delay_sec=data_delay_sec,
        order_reject_rate_pct=await _order_reject_rate_pct(pool),
        daily_loss_pct=await _daily_loss_pct(pool),
    )
