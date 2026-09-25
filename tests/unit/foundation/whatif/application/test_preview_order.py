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

import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.core.portfolio.state_input import PortfolioAggregate
from src.core.risk.decision import RiskOutcome
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
    COMPLIANCE_BUNDLE_INACTIVE,
    COMPLIANCE_MANDATE_MISSING,
    RISK_INPUTS_UNAVAILABLE,
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
# 1. Happy path — both authorities ALLOW
# ---------------------------------------------------------------------------


class TestAllow:
    def test_no_limits_no_restrictions_allows_both(self):
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=_risk_inputs(),
            risk_limits=(),
            mandate=_mandate(),
            now=_NOW,
        )
        assert result.would_be_denied_by == ()
        assert result.reason_codes == {}
        assert result.risk_outcome == RiskOutcome.ALLOW
        assert result.compliance_verdict == ComplianceVerdict.ALLOW
        assert result.impact.symbol == "AAPL"

    def test_risk_limit_within_bound_allows(self):
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=_risk_inputs(),
            risk_limits=(_order_notional_limit(Decimal("100000")),),
            mandate=_mandate(),
            now=_NOW,
        )
        assert "risk" not in result.would_be_denied_by


# ---------------------------------------------------------------------------
# 2. Denial — risk authority
# ---------------------------------------------------------------------------


class TestRiskDenial:
    def test_hard_limit_breach_denies_by_risk(self):
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=_risk_inputs(notional=Decimal("50000")),
            risk_limits=(_order_notional_limit(Decimal("1000"), hard=True),),
            mandate=_mandate(),
            now=_NOW,
        )
        assert result.would_be_denied_by == ("risk",)
        assert result.reason_codes["risk"][0].startswith("RISK_LIMIT_BREACH")
        assert result.risk_outcome == RiskOutcome.DENY

    def test_soft_limit_breach_escalates_and_is_still_flagged(self):
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=_risk_inputs(notional=Decimal("50000")),
            risk_limits=(_order_notional_limit(Decimal("1000"), hard=False),),
            mandate=_mandate(),
            now=_NOW,
        )
        assert result.risk_outcome == RiskOutcome.ESCALATE
        assert "risk" in result.would_be_denied_by


# ---------------------------------------------------------------------------
# 3. Denial — compliance authority
# ---------------------------------------------------------------------------


class TestComplianceDenial:
    def test_restricted_symbol_denies_by_compliance(self):
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=_risk_inputs(),
            risk_limits=(),
            mandate=_mandate(forbidden_assets=("AAPL",)),
            now=_NOW,
        )
        assert result.would_be_denied_by == ("compliance",)
        assert result.reason_codes["compliance"] == ("restricted_list",)
        assert result.compliance_verdict == ComplianceVerdict.DENY

    def test_concentration_breach_denies_by_compliance(self):
        # AAPL goes from 30% to a much larger pct after a big BUY; cap it at 1%.
        big_buy = ProposedTrade(
            symbol="AAPL", notional=Decimal("500000"), side="BUY", strategy_id="STRAT_A"
        )
        result = preview_order(
            before=_make_state(),
            trade=big_buy,
            risk_inputs=_risk_inputs(notional=Decimal("500000")),
            risk_limits=(),
            mandate=_mandate(max_single_instrument_pct=1.0),
            now=_NOW,
        )
        assert "compliance" in result.would_be_denied_by
        assert "concentration" in result.reason_codes["compliance"]

    def test_both_authorities_deny_simultaneously(self):
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=_risk_inputs(notional=Decimal("50000")),
            risk_limits=(_order_notional_limit(Decimal("1000")),),
            mandate=_mandate(forbidden_assets=("AAPL",)),
            now=_NOW,
        )
        assert set(result.would_be_denied_by) == {"risk", "compliance"}


# ---------------------------------------------------------------------------
# 4. Negative tests (>= 3 required for D2) — missing/invalid context is
#    fail-closed, never a silent ALLOW.
# ---------------------------------------------------------------------------


class TestNegativeFailClosed:
    def test_missing_risk_inputs_denies_by_risk(self):
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=None,
            risk_limits=(),
            mandate=_mandate(),
            now=_NOW,
        )
        assert result.would_be_denied_by == ("risk",)
        assert result.reason_codes["risk"] == (RISK_INPUTS_UNAVAILABLE,)
        assert result.risk_outcome is None

    def test_missing_mandate_denies_by_compliance(self):
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=_risk_inputs(),
            risk_limits=(),
            mandate=None,
            now=_NOW,
        )
        assert result.would_be_denied_by == ("compliance",)
        assert result.reason_codes["compliance"] == (COMPLIANCE_MANDATE_MISSING,)
        assert result.compliance_verdict is None

    def test_inactive_mandate_denies_by_compliance(self):
        result = preview_order(
            before=_make_state(),
            trade=BUY_AAPL,
            risk_inputs=_risk_inputs(),
            risk_limits=(),
            mandate=_mandate(state=MandateRevisionState.PAUSED),
            now=_NOW,
        )
        assert result.would_be_denied_by == ("compliance",)
        assert result.reason_codes["compliance"] == (COMPLIANCE_BUNDLE_INACTIVE,)

    def test_invalid_trade_notional_raises_before_any_gate_runs(self):
        """Negative: a structurally invalid trade must fail loud (ValueError
        from UX-9's delta_impact), never silently produce an ALLOW result."""
        bad_trade = ProposedTrade(symbol="AAPL", notional=Decimal("0"), side="BUY")
        with pytest.raises(ValueError):
            preview_order(
                before=_make_state(),
                trade=bad_trade,
                risk_inputs=_risk_inputs(),
                risk_limits=(),
                mandate=_mandate(),
                now=_NOW,
            )


# ---------------------------------------------------------------------------
# 5. Failure-injection test (>= 1 required for D2)
# ---------------------------------------------------------------------------


class TestFailureInjection:
    def test_risk_engine_exception_propagates_instead_of_silently_allowing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Inject: the pure R-14 rule engine itself raises (e.g. a corrupted
        `ExposureLimit`). preview_order must not swallow that into a
        default-ALLOW result — it propagates, which is the fail-closed
        posture for an internal failure this leaf cannot itself classify."""
        import src.foundation.whatif.application.preview_order as module

        def _boom(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("injected R-14 failure")

        monkeypatch.setattr(module, "check_exposure_limits", _boom)

        with pytest.raises(RuntimeError, match="injected R-14 failure"):
            preview_order(
                before=_make_state(),
                trade=BUY_AAPL,
                risk_inputs=_risk_inputs(),
                risk_limits=(),
                mandate=_mandate(),
                now=_NOW,
            )


# ---------------------------------------------------------------------------
# 6. Performance assertion (>= 1 required for D2)
# ---------------------------------------------------------------------------


class TestPerformance:
    @pytest.mark.perf
    def test_preview_order_p99_under_5ms_pre_trade_gate_budget(self):
        """Budget: ADR-2026-09-09-C Decision 1 §23 "사전거래 게이트 p99 5ms" —
        preview_order runs the same two authorities a real pre-trade gate
        does, read-only, so it must fit the same budget."""
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

        iterations = 500
        samples: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            preview_order(
                before=before,
                trade=BUY_AAPL,
                risk_inputs=risk_inputs,
                risk_limits=(),
                mandate=mandate,
                now=_NOW,
            )
            samples.append((time.perf_counter() - start) * 1000)

        samples.sort()
        p99_ms = samples[int(iterations * 0.99) - 1]
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
