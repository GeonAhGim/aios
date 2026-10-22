"""L4_strategy_portfolio_backtest_v1.0.md#section 2 row 96, section 9 L20 --
mandate_binding.py tests.

DoD (a)-(g) must all be falsifiable: FORBIDDEN passing as a clamp instead of
a denial, an off-by-one clamp value, a clamp-to-zero silently approving a
zero-quantity order, or reasons ordering drifting between two identical
calls must each fail some test here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.portfolio.mandate_binding import (
    bind,
)
from src.core.portfolio.state_input import PortfolioAggregate
from src.foundation.mandates.contracts.v1 import (
    Autonomy,
    MandateRevisionState,
    MandateRevisionView,
)
from tests.conftest import PerfBudget

_AS_OF = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _revision_hash(
    *,
    max_total_exposure_pct: float,
    max_single_instrument_pct: float,
    min_cash_buffer_pct: float,
    max_daily_loss_pct: float,
    allowed_autonomy: Autonomy,
    forbidden_assets: list[str],
) -> str:
    payload = "|".join(
        [
            f"{max_total_exposure_pct:.2f}",
            f"{max_single_instrument_pct:.2f}",
            f"{min_cash_buffer_pct:.2f}",
            f"{max_daily_loss_pct:.2f}",
            allowed_autonomy.value,
            ",".join(sorted(forbidden_assets)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mandate(
    *,
    max_total_exposure_pct: float = 100.0,
    max_single_instrument_pct: float = 100.0,
    min_cash_buffer_pct: float = 0.0,
    max_daily_loss_pct: float = 100.0,
    allowed_autonomy: Autonomy = Autonomy.PAPER,
    forbidden_assets: list[str] | None = None,
) -> MandateRevisionView:
    forbidden = forbidden_assets or []
    return MandateRevisionView(
        id=uuid4(),
        mandate_id=uuid4(),
        revision_no=1,
        state=MandateRevisionState.ACTIVE,
        max_total_exposure_pct=max_total_exposure_pct,
        max_single_instrument_pct=max_single_instrument_pct,
        min_cash_buffer_pct=min_cash_buffer_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        allowed_autonomy=allowed_autonomy,
        forbidden_assets=forbidden,
        revision_hash=_revision_hash(
            max_total_exposure_pct=max_total_exposure_pct,
            max_single_instrument_pct=max_single_instrument_pct,
            min_cash_buffer_pct=min_cash_buffer_pct,
            max_daily_loss_pct=max_daily_loss_pct,
            allowed_autonomy=allowed_autonomy,
            forbidden_assets=forbidden,
        ),
        cooling_off_started_at=None,
        created_at=_AS_OF,
        activated_at=_AS_OF,
    )


def agg(
    *,
    total_equity: Decimal = Decimal("10000"),
    per_symbol_pct: dict[str, Decimal] | None = None,
    total_exposure_pct: Decimal = Decimal("0"),
    cash_pct: Decimal = Decimal("100"),
) -> PortfolioAggregate:
    return PortfolioAggregate(
        total_equity=total_equity,
        per_symbol_pct=per_symbol_pct or {},
        per_strategy_pct={},
        total_exposure_pct=total_exposure_pct,
        cash_pct=cash_pct,
        as_of=_AS_OF,
    )


# --- gate-red 재현: 10x CPU 지연 주입으로 단언이 적색으로 넘어가는지 검증 -------


@pytest.mark.perf
def test_bind_goes_red_with_10x_cpu_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현(D2): 성능 예산 단언이 실제로 위반을 포착하는지
    역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    5ms * 10 = 50ms를 초과하는 CPU 시간을 가장하면
    `sample.cpu_ms < 5.0` 단언이 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다(task-11539 감사: 호출 1회 CPU 측정의 0 수렴 결함 방지).
    """
    budget_ms = 5.0

    m = mandate(
        max_single_instrument_pct=20.0,
        max_total_exposure_pct=30.0,
        min_cash_buffer_pct=10.0,
    )
    a = agg(
        total_equity=Decimal("10000"),
        per_symbol_pct={"BTC/USDT": Decimal("5")},
        total_exposure_pct=Decimal("10"),
    )

    def _call_once() -> None:
        bind(qty=Decimal("50"), price=Decimal("100"), symbol="BTC/USDT", agg=a, mandate=m)

    # process_time을 monkeypatch: 호출할 때마다 50ms(10x budget)를 더한다.
    # PerfBudget.sample()은 time.process_time()을 호출하므로 이 값이
    # cpu_ms 측정값에 그대로 반영돼 budget을 10배 초과한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 50.0  # 50ms = 10x budget per call
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.best_of(_call_once, n=5, batch=100)
        assert sample.cpu_ms < budget_ms, (
            f"p99 proxy cpu_ms={sample.cpu_ms:.3f}ms exceeds {budget_ms}ms budget "
            f"(wall_ms={sample.wall_ms:.3f}ms)"
        )
