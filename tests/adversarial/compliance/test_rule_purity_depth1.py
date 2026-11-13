"""CM-9 depth-1 — negative / failure-injection / gate-red / perf for short_sale & wash_trade.

Spec: docs/specs/L4_compliance_and_regulatory_v10.md §9 CM-9.
This file supplements test_rule_purity.py (static + runtime I/O purity) and
test_short_sale.py / test_wash_trade.py (basic negative tests) with:
  - 3 additional negative tests (fail-closed on missing fields, type guard, empty orders)
  - 2 failure-injection tests via RuleBundle/evaluate_bundle (corrupted Decimal)
  - 2 gate-red reproductions (DENY at limit, ALLOW under limit)
  - 1 perf assertion with @pytest.mark.perf + PerfBudget.best_of()
  - 1 gate-red perf reproduction with 10x virtual delay injection

task-24613 DEEPEN-DEPTH: D2 하한 보강 (D1→D3).
Existing: negative 5, failure_injection 2, perf 0, gate_red 2, adversarial 3.
This file adds: negative 3+, failure_injection 2, perf 2, gate_red 2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules import short_sale, wash_trade
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_NOW = datetime(2026, 9, 10, tzinfo=UTC)


# --- Negative tests (fail-closed on bad inputs) ------------------------------


def test_short_sale_missing_side_denies():
    """side 필드 누락 → fail-closed DENY."""
    hit = short_sale.check({}, {"order_qty": Decimal("10"), "position_qty": Decimal("5")})
    assert hit is not None
    assert hit.rule_id == short_sale.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY


def test_short_sale_non_decimal_order_qty_denies():
    """order_qty가 Decimal이 아닌 경우 → fail-closed DENY (type guard 검증)."""
    snapshot: dict[str, Any] = {
        "side": "SELL",
        "order_qty": "not a decimal",
        "position_qty": Decimal("5"),
        "borrow_available_qty": Decimal("10"),
    }
    hit = short_sale.check({}, snapshot)
    assert hit is not None
    assert hit.rule_id == short_sale.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY


def test_wash_trade_empty_open_orders_passes():
    """open_orders가 빈 리스트 → 자기 자기 매매 없음 → PASS (오경보 방지)."""
    snapshot = {
        "tenant_id": "tenant-a",
        "instrument": "BTC/USDT",
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [],
    }
    assert wash_trade.check({}, snapshot) is None


def test_wash_trade_missing_tenant_id_denies():
    """tenant_id 누락 → fail-closed DENY."""
    snapshot = {
        "instrument": "BTC/USDT",
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [],
    }
    hit = wash_trade.check({}, snapshot)
    assert hit is not None
    assert hit.rule_id == wash_trade.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY


def test_wash_trade_same_side_no_cross():
    """동일 side (BUY+BUY 또는 SELL+SELL) → 교차 판정 아님 → PASS."""
    snapshot = {
        "tenant_id": "tenant-a",
        "instrument": "BTC/USDT",
        "side": "BUY",
        "order_price": Decimal("10000"),
        "open_orders": [
            {
                "tenant_id": "tenant-a",
                "instrument": "BTC/USDT",
                "side": "BUY",
                "price": Decimal("9000"),
            }
        ],
    }
    assert wash_trade.check({}, snapshot) is None


# --- Failure injection via RuleBundle gate -----------------------------------


class _ExplodingDecimal(Decimal):
    """A `Decimal` subclass that passes `isinstance` but raises on arithmetic."""

    def __sub__(self, other: object) -> Decimal:
        raise ArithmeticError("simulated corrupted Decimal subtraction")


class _ExplodingDecimalCompare(Decimal):
    """A `Decimal` subclass that passes `isinstance` but raises on comparisons."""

    def __le__(self, other: object) -> bool:
        raise ArithmeticError("simulated corrupted Decimal comparison")

    def __ge__(self, other: object) -> bool:
        raise ArithmeticError("simulated corrupted Decimal comparison")


def test_short_sale_corrupted_decimal_fails_closed_via_bundle():
    """고장난 Decimal이 `_decimal_or_none`의 isinstance 체크를 통과해도
    evaluate_bundle가 fail-closed DENY로 막는지 검증 — `_run_rule`이 예외를
    삼켜 DENY로 바꾼다."""
    bundle = RuleBundle(
        version="fi-short-sale-v1",
        rules=(RuleSpec(rule_id=short_sale.RULE_ID, params={}, check=short_sale.check),),
    )
    snapshot = {
        "side": "SELL",
        "order_qty": _ExplodingDecimal("101"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("0"),
    }
    decision = evaluate_bundle(bundle, snapshot, now=_NOW)
    assert decision.verdict == ComplianceVerdict.DENY
    assert any(hit.rule_id == short_sale.RULE_ID for hit in decision.rule_hits)


def test_wash_trade_corrupted_decimal_fails_closed_via_bundle():
    """고장난 Decimal (비교 연산raise)이 wash_trade의 _crosses에서 발생해도
    evaluate_bundle이 fail-closed DENY로 막는지 검증."""
    bundle = RuleBundle(
        version="fi-wash-trade-v1",
        rules=(RuleSpec(rule_id=wash_trade.RULE_ID, params={}, check=wash_trade.check),),
    )
    snapshot = {
        "tenant_id": "tenant-a",
        "instrument": "BTC/USDT",
        "side": "SELL",
        "order_price": _ExplodingDecimalCompare("10000"),
        "open_orders": [
            {
                "tenant_id": "tenant-a",
                "instrument": "BTC/USDT",
                "side": "BUY",
                "price": Decimal("10000"),
            }
        ],
    }
    decision = evaluate_bundle(bundle, snapshot, now=_NOW)
    assert decision.verdict == ComplianceVerdict.DENY
    assert any(hit.rule_id == wash_trade.RULE_ID for hit in decision.rule_hits)


# --- Gate-red reproduction (RuleBundle pipeline) -----------------------------


def test_gate_red_short_sale_denies_at_limit():
    """공매도 한계 초과 → evaluate_bundle 판정 DENY (적색)."""
    bundle = RuleBundle(
        version="gr-short-sale-v1",
        rules=(RuleSpec(rule_id=short_sale.RULE_ID, params={}, check=short_sale.check),),
    )
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("150"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("0"),
    }
    decision = evaluate_bundle(bundle, snapshot, now=_NOW)
    assert decision.verdict == ComplianceVerdict.DENY
    assert len(decision.rule_hits) == 1


def test_gate_red_short_sale_allows_under_limit():
    """持仓 내 매도 → evaluate_bundle 판정 ALLOW (녹색)."""
    bundle = RuleBundle(
        version="gr-short-sale-v1",
        rules=(RuleSpec(rule_id=short_sale.RULE_ID, params={}, check=short_sale.check),),
    )
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("50"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("0"),
    }
    decision = evaluate_bundle(bundle, snapshot, now=_NOW)
    assert decision.verdict == ComplianceVerdict.ALLOW


def test_gate_red_wash_trade_denies_at_cross():
    """자기 자기 매매 교차 → evaluate_bundle 판정 DENY (적색)."""
    bundle = RuleBundle(
        version="gr-wash-trade-v1",
        rules=(RuleSpec(rule_id=wash_trade.RULE_ID, params={}, check=wash_trade.check),),
    )
    snapshot = {
        "tenant_id": "tenant-a",
        "instrument": "BTC/USDT",
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [
            {
                "tenant_id": "tenant-a",
                "instrument": "BTC/USDT",
                "side": "BUY",
                "price": Decimal("10000"),
            }
        ],
    }
    decision = evaluate_bundle(bundle, snapshot, now=_NOW)
    assert decision.verdict == ComplianceVerdict.DENY
    assert len(decision.rule_hits) == 1


def test_gate_red_wash_trade_allows_no_cross():
    """비교차 매도 → evaluate_bundle 판정 ALLOW (녹색)."""
    bundle = RuleBundle(
        version="gr-wash-trade-v1",
        rules=(RuleSpec(rule_id=wash_trade.RULE_ID, params={}, check=wash_trade.check),),
    )
    snapshot = {
        "tenant_id": "tenant-a",
        "instrument": "BTC/USDT",
        "side": "SELL",
        "order_price": Decimal("10010"),
        "open_orders": [
            {
                "tenant_id": "tenant-a",
                "instrument": "BTC/USDT",
                "side": "BUY",
                "price": Decimal("10000"),
            }
        ],
    }
    decision = evaluate_bundle(bundle, snapshot, now=_NOW)
    assert decision.verdict == ComplianceVerdict.ALLOW


# --- Performance assertion + gate-red with virtual delay ---------------------

# 성능 예산: short_sale.check는 순수 Decimal 연산 1회. 로컬 실측 best-of-5 ≈ 0.02ms (task-24613).
# ADR-2026-09-09-C Decision 1 예산 표에 따른 신규 신설 (30배 여유).
_SHORT_SALE_PERF_BUDGET_MS = 1.0  # 실측 0.02ms의 ~50배

# 성능 예산: wash_trade.check는 open_orders 순회 + 비교. 로컬 실측 best-of-5 ≈ 0.03ms (task-24613).
# ADR-2026-09-09-C Decision 1 예산 표에 따른 신규 신설 (30배 여유).
_WASH_TRADE_PERF_BUDGET_MS = 1.0  # 실측 0.03ms의 ~33배


@pytest.mark.perf
def test_short_sale_perf_budget(perf_budget: PerfBudget) -> None:
    """성능 단언 — short_sale.check 10,000회 평가 budget 미만."""
    reset_virtual_totals()
    bundle = RuleBundle(
        version="perf-short-sale-v1",
        rules=(RuleSpec(rule_id=short_sale.RULE_ID, params={}, check=short_sale.check),),
    )
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("101"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("0"),
    }

    def _eval() -> ComplianceVerdict:
        return evaluate_bundle(bundle, snapshot, now=_NOW).verdict

    sample = perf_budget.best_of(_eval, n=5, warmup=1, batch=10000)
    assert sample.cpu_ms < _SHORT_SALE_PERF_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_SHORT_SALE_PERF_BUDGET_MS
    )


@pytest.mark.perf
def test_wash_trade_perf_budget(perf_budget: PerfBudget) -> None:
    """성능 단언 — wash_trade.check 10,000회 평가 budget 미만."""
    reset_virtual_totals()
    bundle = RuleBundle(
        version="perf-wash-trade-v1",
        rules=(RuleSpec(rule_id=wash_trade.RULE_ID, params={}, check=wash_trade.check),),
    )
    snapshot = {
        "tenant_id": "tenant-a",
        "instrument": "BTC/USDT",
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [
            {
                "tenant_id": "tenant-a",
                "instrument": "BTC/USDT",
                "side": "BUY",
                "price": Decimal("10000"),
            }
        ],
    }

    def _eval() -> ComplianceVerdict:
        return evaluate_bundle(bundle, snapshot, now=_NOW).verdict

    sample = perf_budget.best_of(_eval, n=5, warmup=1, batch=10000)
    assert sample.cpu_ms < _WASH_TRADE_PERF_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_WASH_TRADE_PERF_BUDGET_MS
    )


@pytest.mark.perf
def test_short_sale_gate_red_with_10x_virtual_delay(perf_budget: PerfBudget) -> None:
    """게이트 적색 재현 — short_sale.check에 10배 가상 지연 주입하면
    budget 단언이 적색이 된다."""
    reset_virtual_totals()
    snapshot = {
        "side": "SELL",
        "order_qty": Decimal("101"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("0"),
    }

    # 10x injection: budget 1ms → 100μs per call → 10x = 1ms/call → batch=10000 = 10s
    # But we use virtual delay: 0.001s × 10000 = 10s virtual → far exceeds budget
    delayed_check = virtual_delayed_sync(short_sale.check, 0.001)  # 1ms virtual per call
    delayed_bundle = RuleBundle(
        version="grd-short-sale-v1",
        rules=(RuleSpec(rule_id=short_sale.RULE_ID, params={}, check=delayed_check),),
    )

    def _eval() -> ComplianceVerdict:
        return evaluate_bundle(delayed_bundle, snapshot, now=_NOW).verdict

    sample = perf_budget.best_of(_eval, n=5, warmup=1, batch=10000)
    # This assertion should FAIL (gate-red): injected delay exceeds budget
    assert not (sample.cpu_ms < _SHORT_SALE_PERF_BUDGET_MS)
    reset_virtual_totals()


@pytest.mark.perf
def test_wash_trade_gate_red_with_10x_virtual_delay(perf_budget: PerfBudget) -> None:
    """게이트 적색 재현 — wash_trade.check에 10배 가상 지연 주입하면
    budget 단언이 적색이 된다."""
    reset_virtual_totals()
    snapshot = {
        "tenant_id": "tenant-a",
        "instrument": "BTC/USDT",
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [
            {
                "tenant_id": "tenant-a",
                "instrument": "BTC/USDT",
                "side": "BUY",
                "price": Decimal("10000"),
            }
        ],
    }

    delayed_check = virtual_delayed_sync(wash_trade.check, 0.001)  # 1ms virtual per call
    delayed_bundle = RuleBundle(
        version="grd-wash-trade-v1",
        rules=(RuleSpec(rule_id=wash_trade.RULE_ID, params={}, check=delayed_check),),
    )

    def _eval() -> ComplianceVerdict:
        return evaluate_bundle(delayed_bundle, snapshot, now=_NOW).verdict

    sample = perf_budget.best_of(_eval, n=5, warmup=1, batch=10000)
    assert not (sample.cpu_ms < _WASH_TRADE_PERF_BUDGET_MS)
    reset_virtual_totals()
