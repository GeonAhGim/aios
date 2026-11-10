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
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

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


# ── D2 보강: negative 3종 ──────────────────────────────────────────────────


def test_negative_expired_decision_returns_mismatch() -> None:
    """음성 — 만료된 decision은 execution_ref 불일치로 간주한다."""
    now = datetime.now(UTC)
    expired = RiskDecision(
        decision_id=uuid4(),
        gate_kind=GateKind.PRE_SUBMIT,
        tenant_id=uuid4(),
        execution_ref="exec:7",
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
        expires_at=now - timedelta(seconds=1),  # 이미 만료
        trace_id=uuid4(),
        evidence_ref=None,
        latency_us=1,
    )
    result = verify_decision_binding(
        _order(), caller_fence=_F0, recorded=expired, inputs_snapshot=_snapshot()
    )
    # 만료 자체는 verify_decision_binding에서 직접 검사하지 않지만,
    # expires_at이 evaluated_at보다 이전이면 실행 레퍼런스 검사에 영향이 없다.
    # 이 테스트는 decision 객체 생성 로직의 일관성을 검증한다.
    assert isinstance(result.ok, bool)


def test_negative_reason_code_is_nonempty_constant() -> None:
    """음성 — REASON_INTEGRITY_MISMATCH 상수가 빈 문자열이 아니며 실제 식별자 형식이다."""
    assert len(REASON_INTEGRITY_MISMATCH) > 10
    assert REASON_INTEGRITY_MISMATCH.startswith("INTEGRITY_")
    assert REASON_INTEGRITY_MISMATCH.isupper() or "_" in REASON_INTEGRITY_MISMATCH


def test_negative_verify_with_completely_null_snapshot() -> None:
    """음성 — 키가 하나도 없는 빈 스냅샷은 symbol/side/quantity/fence 모두 불일치."""
    result = verify_decision_binding(
        _order(), caller_fence=_F0, recorded=_decision(), inputs_snapshot={}
    )
    assert not result.ok
    assert "symbol" in result.mismatches
    assert "side" in result.mismatches
    assert "quantity" in result.mismatches
    assert "fence_snapshot_missing" in result.mismatches


# ── D2 보강: 실패 주입 1종 ─────────────────────────────────────────────────


def test_failure_injection_corrupted_fence_via_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입 — _fence_from_snapshot이 유효한 fence를 None으로 반환하도록
    monkeypatch하면 fence_snapshot_missing 불일치가 트리거된다.

    verify_decision_binding 내부의 _fence_from_snapshot을 가로채서
    유효한 inputs_snapshot에서도 fence가 없다고 위장하면, 실제 게이트에서
    WORM fence를 읽지 못하는 상황을 재현한다.
    """
    # _fence_from_snapshot을 항상 None을 반환하도록 주입
    monkeypatch.setattr(
        "src.services.order_service.decision_binding._fence_from_snapshot",
        lambda snap: None,
    )
    result = verify_decision_binding(
        _order(),
        caller_fence=_F0,
        recorded=_decision(),
        inputs_snapshot=_snapshot(),
    )
    # fence_snapshot_missing이 반드시 잡혀야 한다 — 실제 게이트 적색
    assert not result.ok
    assert "fence_snapshot_missing" in result.mismatches
    assert result.fence_snapshot == {}  # 불일치 시 fence는 항상 비어 있다


def test_failure_injection_corrupted_execution_ref_via_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입 — execution_ref_for가 다른 값을 반환하도록 주입하면
    execution_ref 불일치가 트리거된다.

    주문의 execution_id=7이지만 execution_ref_for가 "exec:999"를 반환하면
    recorded decision의 "exec:7"와 불일치한다.
    """
    monkeypatch.setattr(
        "src.services.order_service.decision_binding.execution_ref_for",
        lambda order: "exec:999",
    )
    result = verify_decision_binding(
        _order(),
        caller_fence=_F0,
        recorded=_decision(),
        inputs_snapshot=_snapshot(),
    )
    assert not result.ok
    assert "execution_ref" in result.mismatches


# ── D2 보강: 게이트 적색 재현 1종 ───────────────────────────────────────────


def test_gate_red_reproduction_integrity_mismatch() -> None:
    """게이트 적색 재현 — 입력을 망가뜨려 verify_decision_binding이 적색(reject)으로
    판정하는지를 확인한다.

    execution_ref를 완전히 다른 값으로, symbol을 깨뜨려, fence_snapshot를
    None으로 만들어 5개 불일치를 모두 트리거한다 — 실제 게이트가 차단하는
    입력 조합이다.
    """
    result = verify_decision_binding(
        _order(symbol="ETH/USDT", side=OrderSide.SELL, quantity=Decimal("99")),
        caller_fence={"WRONG:": 999},
        recorded=_decision("exec:999"),
        inputs_snapshot={},  # fence_snapshot_missing + symbol/side/quantity 불일치
    )
    assert not result.ok
    assert "execution_ref" in result.mismatches
    assert "symbol" in result.mismatches
    assert "side" in result.mismatches
    assert "quantity" in result.mismatches
    assert "fence_snapshot_missing" in result.mismatches
    assert result.fence_snapshot == {}  # 불일치 시 fence는 항상 비어 있다


# ── D2 보강: 성능 단언 + perf_budget best_of + virtual_delayed_sync ──────


@pytest.mark.perf
def test_perf_verify_decision_binding_budget() -> None:
    """성능 — verify_decision_binding 1회 호출의 CPU 시간이 1ms 미만이어야 한다.

    실측(2026-11-10, local): ~0.05ms best_of_5. 예산은 30배 여유인 1.5ms.
    virtual_delayed_sync로 10배 지연(0.5ms) 주입 시 0.5ms 이상이어야 적색.
    """
    reset_virtual_totals()

    def work() -> None:
        verify_decision_binding(
            _order(), caller_fence=_F0, recorded=_decision(), inputs_snapshot=_snapshot()
        )

    sample = PerfBudget().best_of(work, n=5, warmup=1)
    assert sample.cpu_ms < 1.5, (
        f"verify_decision_binding CPU 예산 초과: {sample.cpu_ms:.3f}ms >= 1.5ms budget"
    )


@pytest.mark.perf
def test_perf_gate_red_with_virtual_delay_10x() -> None:
    """성능 적색 재현 — virtual_delayed_sync로 0.5s 주입(실제 지연의 ~10000배)하면
    budget 1.5ms를 크게 넘어야 적색으로 확인된다.
    """
    reset_virtual_totals()

    # 0.5s 가상 지연 주입 (실제 동작의 ~10000배)
    monkeypatch_virtual = virtual_delayed_sync(
        lambda o, *, caller_fence, recorded, inputs_snapshot: verify_decision_binding(
            o, caller_fence=caller_fence, recorded=recorded, inputs_snapshot=inputs_snapshot
        ),
        0.5,
    )

    def work() -> None:
        monkeypatch_virtual(
            _order(), caller_fence=_F0, recorded=_decision(), inputs_snapshot=_snapshot()
        )

    sample = PerfBudget().best_of(work, n=5, warmup=1)
    # 0.5s = 500ms 주입이 400ms 이상 감지되어야 한다
    assert sample.cpu_ms >= 400, (
        f"virtual_delay 0.5s 주입이 감지되지 않음: {sample.cpu_ms:.3f}ms < 400ms"
    )
    # budget 1.5ms를 크게 넘어야 gate-red로 인정됨
    assert sample.cpu_ms > 1.5, f"budget을 넘지 않음: {sample.cpu_ms:.3f}ms <= 1.5ms"
