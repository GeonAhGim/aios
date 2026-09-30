from decimal import Decimal
from pathlib import Path

from src.core.safety.heartbeat import write_heartbeat
from src.core.safety.split_brain import Diagnosis, FailureDomain
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


def test_decide_rejects_liquidate_when_db_isolated_despite_market_correlation():
    """negative — DB-only 단절 진단(FD-9.3)일 때는 market_wide_correlated=True여도
    강제청산(LIQUIDATE)을 절대 발행하지 않는다(DB 쓰기가 필요한 청산 요청을
    애초에 만들지 않는다는 불변식)."""
    db_isolated = FailureDomain(
        db_ok=False, exchange_ok=True, main_process_ok=True, diagnosis=Diagnosis.DB_ISOLATED_FAILURE
    )
    decision = decide(
        _snapshot(loss_pct="8"), market_wide_correlated=True, failure_domain=db_isolated
    )
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "db_isolated_liquidate_downgraded"


def test_decide_rejects_unresponsive_halt_when_loss_also_exceeds_threshold():
    """negative — 응답불능과 손실초과가 동시 발생하면 손실 판정이 우선해야
    한다("main_process_unresponsive"로 손실초과를 가리지 않는다)."""
    decision = decide(_snapshot(loss_pct="8", unresponsive_sec=45), market_wide_correlated=True)
    assert decision.action == WatchdogAction.LIQUIDATE
    assert decision.reason != "main_process_unresponsive"


def test_decide_rejects_normal_at_exact_loss_threshold_boundary():
    """negative — 손실률이 임계값과 정확히 같을 때 NORMAL로 잘못 분류하지
    않는다(>= 경계 처리)."""
    decision = decide(_snapshot(loss_pct="7.0"), market_wide_correlated=False)
    assert decision.action != WatchdogAction.NORMAL
    assert decision.action == WatchdogAction.HALT


def test_decide_rejects_normal_at_exact_unresponsive_threshold_boundary():
    """negative — unresponsive_sec이 임계값과 정확히 같을 때 NORMAL로 잘못
    분류하지 않는다(>= 경계 처리)."""
    decision = decide(_snapshot(loss_pct="0", unresponsive_sec=30.0), market_wide_correlated=None)
    assert decision.action != WatchdogAction.NORMAL
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "main_process_unresponsive"


async def test_snapshot_health_check_failure_marks_unhealthy_keeps_last_loss(tmp_path: Path):
    """실패주입 — compute_equity는 성공하지만 health_check가 예외를 던지는
    경우에도 조회 실패로 취급해 마지막 값을 유지하며 exchange_healthy=False로
    표기한다."""
    heartbeat = tmp_path / "hb"
    write_heartbeat(heartbeat)
    call_count = 0

    async def compute_equity():
        return Decimal("1000")

    async def health_check():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True
        raise TimeoutError("health check timed out")

    service = WatchdogService(
        compute_equity=compute_equity, health_check=health_check, heartbeat_path=heartbeat
    )
    first = await service.take_snapshot()
    second = await service.take_snapshot()

    assert first.exchange_healthy is True
    assert second.exchange_healthy is False
    assert second.loss_pct == first.loss_pct
