"""UX-10: tests for preview_order() in whatif/application/preview_order.py.

Depth=D2 — per ADR-2026-09-09-C Decision 1:
  - negative tests >= 3
  - failure-injection test >= 1
  - performance assertion >= 1 (budget: pre-trade gate p99 5ms,
    ADR-2026-09-09-C Decision 1 §23 "사전거래 게이트 p99 5ms")
  - gate-red reproduction >= 1

UX-A2 write-freedom itself is proven separately in
tests/adversarial/whatif/test_preview_order_write_free.py (static AST +
adversarial injection); this file only covers functional correctness of the
risk/compliance gate composition.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.core.portfolio.state_input import PortfolioAggregate
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.limits import ExposureLimit, LimitMetric, LimitScope
from src.foundation.mandates.contracts.v1 import (
    Autonomy,
    ComplianceVerdict,
    MandateRevisionState,
    MandateRevisionView,
)
from src.foundation.whatif.application.preview_order import (
    preview_order,
)
from src.foundation.whatif.domain.impact import ProposedTrade

_NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = UUID(int=7)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_state(
    total_equity: Decimal = Decimal("1000000"),
    per_symbol_pct: dict[str, Decimal] | None = None,
) -> PortfolioAggregate:
    return PortfolioAggregate(
        total_equity=total_equity,
        per_symbol_pct=per_symbol_pct or {"AAPL": Decimal("30")},
        per_strategy_pct={"STRAT_A": Decimal("30")},
        total_exposure_pct=Decimal("30"),
        cash_pct=Decimal("70"),
        as_of=_NOW,
    )


BUY_AAPL = ProposedTrade(
    symbol="AAPL", notional=Decimal("50000"), side="BUY", strategy_id="STRAT_A"
)


def _mandate(
    *,
    state: MandateRevisionState = MandateRevisionState.ACTIVE,
    forbidden_assets: tuple[str, ...] = (),
    max_single_instrument_pct: float = 100.0,
) -> MandateRevisionView:
    return MandateRevisionView(
        id=UUID(int=1),
        mandate_id=UUID(int=2),
        revision_no=1,
        state=state,
        max_total_exposure_pct=100.0,
        max_single_instrument_pct=max_single_instrument_pct,
        min_cash_buffer_pct=0.0,
        max_daily_loss_pct=100.0,
        allowed_autonomy=Autonomy.PAPER,
        forbidden_assets=list(forbidden_assets),
        revision_hash="a" * 64,
        cooling_off_started_at=None,
        created_at=None,
        activated_at=None,
    )


def _risk_inputs(*, notional: Decimal = Decimal("50000")) -> RiskInputs:
    return RiskInputs(
        tenant_id=_TENANT,
        execution_ref="exec:1",
        certified_badge=None,
        allocated_capital=None,
        intent=OrderIntent(
            symbol="AAPL",
            asset_class="EQUITY",
            side="BUY",
            quantity=Decimal("100"),
            ref_price=Decimal("500"),
            notional=notional,
            reduce_only=False,
            strategy_id="STRAT_A",
            strategy_version="v1",
            capital_pct=Decimal("5"),
        ),
        equity=EquityInputs(as_of=_NOW, total_equity=Decimal("1000000")),
        exposure=ExposureSnapshot(as_of=_NOW),
        stats=StatsInputs(as_of=_NOW),
        activity=ActivityInputs(),
        safety=SafetyInputs(),
        as_of=_NOW,
    )


def _order_notional_limit(limit_value: Decimal, *, hard: bool = True) -> ExposureLimit:
    return ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="AAPL",
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=limit_value,
        hard=hard,
        limit_id=uuid4(),
    )


# ---------------------------------------------------------------------------
# 6. Performance assertion (>= 1 required for D2)
# ---------------------------------------------------------------------------


class TestPerformance:
    @pytest.mark.perf
    def test_preview_order_p99_under_5ms_pre_trade_gate_budget(self, perf_budget) -> None:
        """Budget: ADR-2026-09-09-C Decision 1 §23 "사전거래 게이트 p99 5ms" —
        preview_order runs the same two authorities a real pre-trade gate
        does, read-only, so it must fit the same budget.

        task-11800: raw perf_counter() → perf_budget.cpu_ms 전환.
        batch=10으로 500회 → 총 5000 호출, 평균 10회 묶어 호출당 값 추출.
        단위: ms(예산도 ms).
        """
        before = _make_state()
        risk_inputs = _risk_inputs()
        mandate = _mandate()

        # Warm-up
        preview_order(
            before=before,
            trade=BUY_AAPL,
            risk_inputs=risk_inputs,
            risk_limits=(),
            mandate=mandate,
            now=_NOW,
        )

        def _run_once() -> None:
            preview_order(
                before=before,
                trade=BUY_AAPL,
                risk_inputs=risk_inputs,
                risk_limits=(),
                mandate=mandate,
                now=_NOW,
            )

        samples = [s.cpu_ms for s in perf_budget.samples(_run_once, n=500, batch=10)]
        samples.sort()
        p99_ms = samples[int(len(samples) * 0.99) - 1]
        assert p99_ms < 5.0, f"preview_order p99 = {p99_ms:.3f} ms, budget is 5 ms"


# ---------------------------------------------------------------------------
# 7. Gate-red reproduction (>= 1 required for D2)
# ---------------------------------------------------------------------------


class TestGateRed:
    def test_forbidden_symbol_reproduces_the_real_cm8_restricted_list_deny(self):
        """Gate-red: reproduce the exact rule id (`restricted_list`) the real
        CM-8 order-submission path would deny with, so a preview never shows
        "allowed" for an order the live gate would actually block."""
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=_risk_inputs(),
            risk_limits=(),
            mandate=_mandate(forbidden_assets=("AAPL", "XYZ")),
            now=_NOW,
        )
        assert result.compliance_verdict == ComplianceVerdict.DENY
        assert result.reason_codes["compliance"] == ("restricted_list",)
