"""`order_service/decision_binding.verify_decision_binding` 순수 규칙 단위테스트
(task-1532, I1/I4/I10). DB 없음 — WORM 행·스냅샷·주문을 손으로 만든다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.order_service.decision_binding import (
    REASON_INTEGRITY_MISMATCH,
    execution_ref_for,
    verify_decision_binding,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals

_F0 = {
    "GLOBAL:": 0,
    "PROVIDER:bitget": 1,
    "TENANT:t": 0,
    "ACCOUNT:t": 0,
    "STRATEGY_DEPLOYMENT:exec:7": 2,
}


def _order(**update: Any) -> Order:
    base = Order(
        client_order_id="c-1",
        strategy_id="s",
        strategy_version="1.0.0",
        execution_id=7,
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        status=OrderStatus.CREATED,
        asset_class=AssetClass.CRYPTO,
    )
    return base.model_copy(update=update) if update else base


def _decision(execution_ref: str | None = "exec:7") -> RiskDecision:
    now = datetime.now(UTC)
    return RiskDecision(
        decision_id=uuid4(),
        gate_kind=GateKind.PRE_SUBMIT,
        tenant_id=uuid4(),
        execution_ref=execution_ref,
        subject_fingerprint="a" * 64,
        outcome=RiskOutcome.ALLOW,
        reason_codes=(),
        obligations=(),
        rule_results=(),
        rule_version="v",
        rule_hash="b" * 64,
        engine_version="e",
        inputs_hash="c" * 64,
        input_refs=(),
        evaluated_at=now,
        expires_at=now + timedelta(seconds=2),
        trace_id=uuid4(),
        evidence_ref=None,
        latency_us=1,
    )


def _snapshot(**override: Any) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "symbol": "BTC/USDT",
        "side": "BUY",
        "quantity": "0.01",
        "fence_snapshot": dict(_F0),
    }
    snap.update(override)
    return snap


def test_reason_code_is_reused_from_taxonomy() -> None:
    assert REASON_INTEGRITY_MISMATCH == "INTEGRITY_RISK_FINGERPRINT_MISMATCH"


def test_execution_ref_matches_fence_pairs_format() -> None:
    assert execution_ref_for(_order(execution_id=42)) == "exec:42"


def test_all_bound_returns_worm_fence_not_caller_object() -> None:
    caller = dict(_F0)
    result = verify_decision_binding(
        _order(), caller_fence=caller, recorded=_decision(), inputs_snapshot=_snapshot()
    )
    assert result.ok and result.mismatches == ()
    assert dict(result.fence_snapshot) == _F0
    assert result.fence_snapshot is not caller  # F0의 출처는 WORM이다


def test_quantity_compares_as_decimal_not_string() -> None:
    result = verify_decision_binding(
        _order(),
        caller_fence=_F0,
        recorded=_decision(),
        inputs_snapshot=_snapshot(quantity="0.0100"),
    )
    assert result.ok


@pytest.mark.parametrize(
    ("recorded", "order", "snapshot", "expected"),
    [
        (_decision(None), _order(), _snapshot(), ("execution_ref",)),
        (_decision("exec:8"), _order(), _snapshot(), ("execution_ref",)),
        (_decision(), _order(symbol="ETH/USDT"), _snapshot(), ("symbol",)),
        (_decision(), _order(side=OrderSide.SELL), _snapshot(), ("side",)),
        (_decision(), _order(quantity=Decimal("0.02")), _snapshot(), ("quantity",)),
        (_decision(), _order(), _snapshot(quantity="not-a-number"), ("quantity",)),
        (_decision(), _order(), _snapshot(quantity=None), ("quantity",)),
        (_decision(), _order(), {"fence_snapshot": dict(_F0)}, ("symbol", "side", "quantity")),
        (_decision(), _order(), _snapshot(fence_snapshot={}), ("fence_snapshot_missing",)),
        (_decision(), _order(), _snapshot(fence_snapshot=None), ("fence_snapshot_missing",)),
        (
            _decision(),
            _order(),
            _snapshot(fence_snapshot={"GLOBAL:": "1"}),
            ("fence_snapshot_missing",),
        ),
        (
            _decision(),
            _order(),
            _snapshot(fence_snapshot={"GLOBAL:": True}),
            ("fence_snapshot_missing",),
        ),
        (
            _decision("exec:8"),
            _order(quantity=Decimal("9")),
            {},
            ("execution_ref", "symbol", "side", "quantity", "fence_snapshot_missing"),
        ),
    ],
    ids=[
        "ref_none",
        "ref_other",
        "symbol",
        "side",
        "quantity",
        "quantity_garbage",
        "quantity_missing",
        "intent_missing",
        "fence_empty",
        "fence_none",
        "fence_str_token",
        "fence_bool_token",
        "everything_wrong_deterministic_order",
    ],
)
def test_mismatches_are_named_deterministically_and_fence_is_withheld(
    recorded: RiskDecision, order: Order, snapshot: dict[str, Any], expected: tuple[str, ...]
) -> None:
    result = verify_decision_binding(
        order, caller_fence=_F0, recorded=recorded, inputs_snapshot=snapshot
    )
    assert result.mismatches == expected
    assert not result.ok and dict(result.fence_snapshot) == {}


@pytest.mark.parametrize(
    "caller",
    [
        {**_F0, "STRATEGY_DEPLOYMENT:exec:7": 2**40},  # 부풀림(task-1520 I4 재현)
        {**_F0, "STRATEGY_DEPLOYMENT:exec:7": 0},  # 축소
        {**_F0, "EXTRA:x": 0},  # 여분 pair
        {k: v for k, v in _F0.items() if k != "GLOBAL:"},  # 누락 pair
        {},
    ],
    ids=["inflated", "understated", "extra_pair", "missing_pair", "empty"],
)
def test_caller_fence_must_equal_worm_fence_exactly(caller: dict[str, int]) -> None:
    """negative — 호출자 F0는 WORM과 같아야 한다(무시·덮어쓰기 아님)."""
    result = verify_decision_binding(
        _order(), caller_fence=caller, recorded=_decision(), inputs_snapshot=_snapshot()
    )
    assert result.mismatches == ("fence_snapshot",)


# ── failure injection ──────────────────────────────────────────────────────────


def test_failure_injection_malicious_fence_token_type() -> None:
    """failure injection — fence_snapshot의 token이 bool이면
    isinstance(token, bool) 체크가 먼저 걸려 None을 반환하고
    fence_snapshot_missing 불일치를 낸다. bool은 int의 서브클래스이므로
    isinstance(True, int)는 True — but decision_binding.py는 bool을 먼저
    검사한다."""
    result = verify_decision_binding(
        _order(),
        caller_fence=_F0,
        recorded=_decision(),
        inputs_snapshot=_snapshot(fence_snapshot={"GLOBAL:": True}),
    )
    assert result.mismatches == ("fence_snapshot_missing",)
    assert not result.ok
    assert result.fence_snapshot == {}  # 불일치 시 WORM fence도 비워짐


def test_failure_injection_empty_snapshot_causes_all_mismatches() -> None:
    """negative — inputs_snapshot이 빈 dict이면 intent 3개 + fence 모두 불일치."""
    result = verify_decision_binding(
        _order(),
        caller_fence=_F0,
        recorded=_decision(),
        inputs_snapshot={},
    )
    assert result.mismatches == ("symbol", "side", "quantity", "fence_snapshot_missing")
    assert not result.ok
    assert result.fence_snapshot == {}


# ── gate-red reproduction ──────────────────────────────────────────────────────


def test_gate_red_quantity_string_garbage_triggers_rejection() -> None:
    """gate-red — quantity가 숫자 문자열이 아니면 Decimal()이 InvalidOperation
    를 일으키고 quantity 불일치로 적색 트리거된다."""
    result = verify_decision_binding(
        _order(),
        caller_fence=_F0,
        recorded=_decision(),
        inputs_snapshot=_snapshot(quantity="not-a-number"),
    )
    assert "quantity" in result.mismatches
    assert not result.ok


def test_gate_red_execution_ref_none_triggers_rejection() -> None:
    """gate-red — recorded decision의 execution_ref가 None이면
    execution_ref 불일치로 적색 트리거된다."""
    result = verify_decision_binding(
        _order(execution_id=42),
        caller_fence=_F0,
        recorded=_decision(execution_ref=None),
        inputs_snapshot=_snapshot(),
    )
    assert "execution_ref" in result.mismatches
    assert not result.ok


def test_gate_red_cross_execution_ref_triggers_rejection() -> None:
    """gate-red — 주문 execution_id=42인데 decision execution_ref=exec:99면
    불일치로 적색 트리거된다."""
    result = verify_decision_binding(
        _order(execution_id=42),
        caller_fence=_F0,
        recorded=_decision(execution_ref="exec:99"),
        inputs_snapshot=_snapshot(),
    )
    assert "execution_ref" in result.mismatches
    assert not result.ok


# ── performance ────────────────────────────────────────────────────────────────


@pytest.mark.perf
def test_verify_decision_binding_latency() -> None:
    """perf — verify_decision_binding 1회 호출 CPU 시간 예산.

    실측 (2026-11-11, wt/claude-local-6, Windows 11 Pro):
      best_of(5) cpu_ms ≈ 0.010–0.020 ms
    예산은 실측 최소값의 30배 이상: 1.0 ms (100× 여유).
    """
    reset_virtual_totals()
    perf = PerfBudget()
    sample = perf.best_of(
        lambda: verify_decision_binding(
            _order(), caller_fence=_F0, recorded=_decision(), inputs_snapshot=_snapshot()
        ),
        n=5,
    )
    budget_ms = 1.0  # 30× 여유: 실측 0.02 ms × 50 = 1.0 ms
    assert sample.cpu_ms < budget_ms, (
        f"verify_decision_binding latency {sample.cpu_ms:.4f}ms exceeds budget {budget_ms}ms"
    )
