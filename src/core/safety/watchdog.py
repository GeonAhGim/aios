"""9.1 / 9.2 — Watchdog 손실·응답불능 감시 + 정지/청산 판정.

Spec: 기능설계문서_v1.20.md#FD-9.1/FD-9.2, 정책문서 8.6-A/8.6-A-1

FD-9.1: 계좌 손실률(5분 롤링 윈도우 peak-to-current)과 메인 프로세스
응답성, 거래소 API 자체 헬스체크를 독립적으로 감시한다.
FD-9.2: 위 관측치로 HALT/LIQUIDATE/NORMAL을 판정한다. Griefing 방어 —
손실이 시장 전체 급변과 상관되면 청산 진행, 고립된 손실(조작 의심)이면
신규진입만 즉시 HALT.

편차: "계좌 손실률"은 여러 자산(BTC/USDT/KRW 등)의 총 equity를 하나의
숫자로 요구하지만, 통화 환산(FX) 계층이 아직 없다 — compute_equity 콜백을
주입받아 "단일 기준 통화로 이미 환산된 총자산"을 호출부가 책임지고
넘기도록 했다(11번 §11.1 FX 원칙이 실제 구현되기 전까지의 임시 경계).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.core.safety.heartbeat import read_heartbeat_age_seconds
from src.core.safety.split_brain import Diagnosis, FailureDomain

logger = logging.getLogger(__name__)

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]
ComputeEquityFn = Callable[[], Awaitable[Decimal]]
HealthCheckFn = Callable[[], Awaitable[bool]]

DEFAULT_LOSS_THRESHOLD_PCT = Decimal("7.0")
DEFAULT_UNRESPONSIVE_SEC_THRESHOLD = 30.0
DEFAULT_WINDOW_SECONDS = 5 * 60.0


class WatchdogSnapshot(BaseModel):
    loss_pct: Decimal
    unresponsive_sec: float
    exchange_healthy: bool


class WatchdogAction(str, Enum):
    NORMAL = "NORMAL"
    HALT = "HALT"
    LIQUIDATE = "LIQUIDATE"


class WatchdogDecision(BaseModel):
    action: WatchdogAction
    reason: str


class _EquityWindow:
    """5분 롤링 윈도우의 peak-to-current 손실률(§FD-9.1 처리단계1).

    clock은 기본값이 time.monotonic이라 운영 동작은 그대로다 — 9.7 Watchdog
    오탐 시뮬레이터(watchdog_simulator.py)가 과거 데이터를 실시간 대기 없이
    재생하려고 가짜 시계를 주입할 수 있도록만 열어둔다."""

    def __init__(
        self, window_seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._window_seconds = window_seconds
        self._clock = clock
        self._readings: list[tuple[float, Decimal]] = []

    def record(self, equity: Decimal) -> None:
        now = self._clock()
        self._readings.append((now, equity))
        cutoff = now - self._window_seconds
        self._readings = [(t, e) for t, e in self._readings if t >= cutoff]

    def loss_pct(self) -> Decimal:
        if not self._readings:
            return Decimal("0")
        peak = max(e for _, e in self._readings)
        current = self._readings[-1][1]
        if peak <= 0:
            return Decimal("0")
        return (peak - current) / peak * 100


class WatchdogService:
    def __init__(
        self,
        *,
        compute_equity: ComputeEquityFn,
        health_check: HealthCheckFn,
        heartbeat_path: Path,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._compute_equity = compute_equity
        self._health_check = health_check
        self._heartbeat_path = heartbeat_path
        self._equity_window = _EquityWindow(window_seconds, clock=clock)
        self._last_snapshot: WatchdogSnapshot | None = None

    async def take_snapshot(self) -> WatchdogSnapshot:
        """FD-9.1 예외상황 — account_state 조회 실패 시 마지막 값을 유지하되
        exchange_healthy=False로 표기한다(조회 실패를 "손실 없음"으로
        오판하지 않는다)."""
        unresponsive_sec = read_heartbeat_age_seconds(self._heartbeat_path)
        try:
            equity = await self._compute_equity()
            exchange_healthy = await self._health_check()
        except Exception:  # noqa: BLE001
            logger.exception("Watchdog: 계좌 상태 조회 실패")
            if self._last_snapshot is None:
                snapshot = WatchdogSnapshot(
                    loss_pct=Decimal("0"),
                    unresponsive_sec=unresponsive_sec,
                    exchange_healthy=False,
                )
            else:
                snapshot = self._last_snapshot.model_copy(
                    update={"exchange_healthy": False, "unresponsive_sec": unresponsive_sec}
                )
            self._last_snapshot = snapshot
            return snapshot

        self._equity_window.record(equity)
        snapshot = WatchdogSnapshot(
            loss_pct=self._equity_window.loss_pct(),
            unresponsive_sec=unresponsive_sec,
            exchange_healthy=exchange_healthy,
        )
        self._last_snapshot = snapshot
        return snapshot


def decide(
    snapshot: WatchdogSnapshot,
    *,
    market_wide_correlated: bool | None,
    failure_domain: FailureDomain | None = None,
    loss_threshold_pct: Decimal = DEFAULT_LOSS_THRESHOLD_PCT,
    unresponsive_sec_threshold: float = DEFAULT_UNRESPONSIVE_SEC_THRESHOLD,
) -> WatchdogDecision:
    """FD-9.2 decision logic. `market_wide_correlated` is the result of
    FD-2.6/9.5 (the delegated formula is
    `market_correlation.is_market_wide_move`) — it takes one of three
    values: True (correlated with a market-wide move) / False (an
    isolated loss) / None (undecidable, insufficient data). None is
    treated the same as "suspected manipulation" and handled on the
    safe side (the exceptional-case principle — an inability to decide
    is never treated as normal).

    `failure_domain` is the result of the FD-9.3 split-brain diagnosis
    (§4.3/§6 "DB-only outage"). When only the DB has dropped out, the
    exchange API is treated as the provisional source of truth and the
    account is excluded from forced liquidation — with this diagnosis, a
    LIQUIDATE decision is downgraded to HALT (so that a liquidation
    request requiring a DB write is never created in the first place)."""
    unresponsive = snapshot.unresponsive_sec >= unresponsive_sec_threshold
    if unresponsive and snapshot.loss_pct < loss_threshold_pct:
        return WatchdogDecision(action=WatchdogAction.HALT, reason="main_process_unresponsive")

    if snapshot.loss_pct >= loss_threshold_pct:
        if market_wide_correlated is True:
            db_isolated = (
                failure_domain is not None
                and failure_domain.diagnosis == Diagnosis.DB_ISOLATED_FAILURE
            )
            if db_isolated:
                return WatchdogDecision(
                    action=WatchdogAction.HALT, reason="db_isolated_liquidate_downgraded"
                )
            # Phase 1은 SOR 미구현(06번 §6.4) — 정책문서 8.6-A-1의 "분할 실행
            # 불가능 시 즉시 시장가 청산" 조항으로 시장가 폴백을 정당화.
            return WatchdogDecision(
                action=WatchdogAction.LIQUIDATE, reason="market_wide_correlated_loss"
            )
        return WatchdogDecision(
            action=WatchdogAction.HALT, reason="isolated_loss_suspected_manipulation"
        )

    return WatchdogDecision(action=WatchdogAction.NORMAL, reason="within_thresholds")
