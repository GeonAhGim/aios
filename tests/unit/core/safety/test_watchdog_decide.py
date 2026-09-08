from decimal import Decimal

from src.core.safety.split_brain import Diagnosis, FailureDomain
from src.core.safety.watchdog import WatchdogAction, WatchdogSnapshot, decide


def _snapshot(loss_pct="8", unresponsive_sec=0.0, exchange_healthy=True) -> WatchdogSnapshot:
    return WatchdogSnapshot(
        loss_pct=Decimal(loss_pct),
        unresponsive_sec=unresponsive_sec,
        exchange_healthy=exchange_healthy,
    )


def _failure_domain(diagnosis: Diagnosis, *, db_ok: bool = True) -> FailureDomain:
    return FailureDomain(
        db_ok=db_ok, exchange_ok=True, main_process_ok=True, diagnosis=diagnosis
    )


def test_no_failure_domain_liquidates_as_before():
    decision = decide(_snapshot(), market_wide_correlated=True, failure_domain=None)
    assert decision.action == WatchdogAction.LIQUIDATE


def test_normal_failure_domain_does_not_downgrade_liquidate():
    decision = decide(
        _snapshot(),
        market_wide_correlated=True,
        failure_domain=_failure_domain(Diagnosis.NORMAL),
    )
    assert decision.action == WatchdogAction.LIQUIDATE


def test_db_isolated_failure_downgrades_liquidate_to_halt():
    decision = decide(
        _snapshot(),
        market_wide_correlated=True,
        failure_domain=_failure_domain(Diagnosis.DB_ISOLATED_FAILURE, db_ok=False),
    )
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "db_isolated_liquidate_downgraded"


def test_db_isolated_failure_does_not_affect_already_halt_decision():
    decision = decide(
        _snapshot(),
        market_wide_correlated=False,
        failure_domain=_failure_domain(Diagnosis.DB_ISOLATED_FAILURE, db_ok=False),
    )
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "isolated_loss_suspected_manipulation"


def test_db_isolated_failure_does_not_affect_normal_decision():
    decision = decide(
        _snapshot(loss_pct="1"),
        market_wide_correlated=None,
        failure_domain=_failure_domain(Diagnosis.DB_ISOLATED_FAILURE, db_ok=False),
    )
    assert decision.action == WatchdogAction.NORMAL
