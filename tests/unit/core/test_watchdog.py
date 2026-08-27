from decimal import Decimal
from pathlib import Path

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.watchdog import (
    WatchdogAction,
    WatchdogService,
    WatchdogSnapshot,
    decide,
)


async def test_snapshot_computes_loss_pct_from_equity_drawdown(tmp_path: Path):
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)
    equities = iter([Decimal("1000"), Decimal("930")])

    async def compute_equity():
        return next(equities)

    async def health_check():
        return True

    service = WatchdogService(
        compute_equity=compute_equity, health_check=health_check, heartbeat_path=heartbeat
    )
    await service.take_snapshot()
    snapshot = await service.take_snapshot()

    assert snapshot.loss_pct == Decimal("7")  # (1000-930)/1000*100
    assert snapshot.exchange_healthy is True


async def test_snapshot_missing_heartbeat_reports_infinite_unresponsive(tmp_path: Path):
    async def compute_equity():
        return Decimal("1000")

    async def health_check():
        return True

    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=health_check,
        heartbeat_path=tmp_path / "missing",
    )
    snapshot = await service.take_snapshot()
    assert snapshot.unresponsive_sec == float("inf")


async def test_snapshot_query_failure_keeps_last_value_but_marks_unhealthy(tmp_path: Path):
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)
    call_count = 0

    async def compute_equity():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Decimal("1000")
        raise ConnectionError("exchange down")

    async def health_check():
        return True

    service = WatchdogService(
        compute_equity=compute_equity, health_check=health_check, heartbeat_path=heartbeat
    )
    first = await service.take_snapshot()
    second = await service.take_snapshot()

    assert first.exchange_healthy is True
    assert second.exchange_healthy is False
    assert second.loss_pct == first.loss_pct  # 마지막 값 유지, 손실 없음으로 오판 안 함


async def test_snapshot_first_call_failure_defaults_safely(tmp_path: Path):
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)

    async def compute_equity():
        raise ConnectionError("down from the start")

    async def health_check():
        return True

    service = WatchdogService(
        compute_equity=compute_equity, health_check=health_check, heartbeat_path=heartbeat
    )
    snapshot = await service.take_snapshot()
    assert snapshot.exchange_healthy is False
    assert snapshot.loss_pct == Decimal("0")


def _snapshot(loss_pct="0", unresponsive_sec=0.0, exchange_healthy=True) -> WatchdogSnapshot:
    return WatchdogSnapshot(
        loss_pct=Decimal(loss_pct),
        unresponsive_sec=unresponsive_sec,
        exchange_healthy=exchange_healthy,
    )


def test_decide_normal_within_thresholds():
    decision = decide(_snapshot(loss_pct="2"), market_wide_correlated=None)
    assert decision.action == WatchdogAction.NORMAL


def test_decide_halts_on_unresponsive_without_loss():
    decision = decide(_snapshot(loss_pct="1", unresponsive_sec=45), market_wide_correlated=None)
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "main_process_unresponsive"


def test_decide_liquidates_on_market_wide_correlated_loss():
    decision = decide(_snapshot(loss_pct="8"), market_wide_correlated=True)
    assert decision.action == WatchdogAction.LIQUIDATE


def test_decide_halts_only_on_isolated_loss():
    decision = decide(_snapshot(loss_pct="8"), market_wide_correlated=False)
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "isolated_loss_suspected_manipulation"


def test_decide_treats_undeterminable_correlation_as_isolated():
    """예외상황 — FD-2.6 판정 불가 시 안전한 쪽(조작 의심)으로 기본 처리."""
    decision = decide(_snapshot(loss_pct="8"), market_wide_correlated=None)
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "isolated_loss_suspected_manipulation"
