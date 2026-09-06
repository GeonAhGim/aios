"""L4-16 `unknown_resolver.resolve_unknown` 경계값·fail-closed 적대적 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F5-a, §9 L4-16
DoD("NOT_FOUND 2회+120s → FAILED; 상한 → safety control"), I-01.

전부 실DB 없이 순수 논리만 검증한다 — `order_repo`에 인메모리 대역
(`_FakeOrderRepo`)을 주입하고 `pool`은 `conn.transaction()`만 지원하는
가짜(`_FakePool`)를 쓴다(모듈 docstring "sleep/실시간 의존 금지"와 동일
원칙 — 시각은 전부 고정 `clock` 람다, `sleep`은 즉시 반환하는 no-op).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.foundation.risk_gate.domain.models import SafetyControl, SafetyControlState, SafetyScope
from src.services.oms.application import unknown_resolver
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.errors import InvalidOrderTransitionError

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _no_sleep(seconds: float) -> None:
    return None


def _order_view(**overrides: Any) -> OrderView:
    fields: dict[str, Any] = dict(
        order_id=uuid4(),
        tenant_id=uuid4(),
        execution_id=1,
        client_order_id=f"c-{uuid4().hex}",
        exchange_order_id=None,
        symbol="BTC/USDT",
        venue_symbol="BTCUSDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force="GTC",
        quantity=Decimal("1"),
        price=None,
        status=OrderStatus.UNKNOWN,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        fee_total=None,
        fee_currency=None,
        version=3,
        parent_order_id=None,
        algo_run_id=None,
        unknown_since=BASE,
        provider_order_date=None,
        created_at=BASE,
        updated_at=BASE,
    )
    fields.update(overrides)
    return OrderView(**fields)


def _order(**overrides: Any) -> Order:
    fields: dict[str, Any] = dict(
        client_order_id=f"c-{uuid4().hex}",
        strategy_id="strat-1",
        strategy_version="1.0.0",
        execution_id=1,
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.CRYPTO,
        status=OrderStatus.ACKNOWLEDGED,
    )
    fields.update(overrides)
    return Order(**fields)


class _FakeTx:
    async def start(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _FakeConn:
    def transaction(self) -> _FakeTx:
        return _FakeTx()


class _ConnCtx:
    async def __aenter__(self) -> _FakeConn:
        return _FakeConn()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    def acquire(self) -> _ConnCtx:
        return _ConnCtx()


class _FakeOrderRepo:
    """`OrderRepoPort` 대역 — 단일 주문 상태를 메모리에 들고 `transition()`
    호출을 그대로 반영한다. `ALLOWED` 위반은 프로덕션 구현(order_repository.py)이
    이미 검증하므로 여기서는 재현하지 않고 `state_machine`의 자체 검증에
    맡긴다(호출부가 잘못된 target을 넘기면 그 assert가 먼저 걸린다)."""

    def __init__(self, order: OrderView) -> None:
        self.order = order
        self.events: list[str] = []

    async def get_for_update(self, conn: object, order_id: UUID) -> OrderView:
        assert order_id == self.order.order_id
        return self.order

    async def find_by_scope_hash(self, conn: object, scope_hash: str) -> OrderView | None:
        raise NotImplementedError

    async def transition(
        self,
        conn: object,
        *,
        order_id: UUID,
        expected_status: OrderStatus,
        expected_version: int,
        new_status: OrderStatus,
        patch: dict[str, Any],
        event: Any,
    ) -> OrderView:
        assert expected_status is self.order.status
        assert expected_version == self.order.version
        updated = self.order.model_copy(
            update={**patch, "status": new_status, "version": self.order.version + 1}
        )
        self.order = updated
        self.events.append(event.event)
        return updated


class _LookupAdapter:
    def __init__(
        self, *, results: list[Order | None], open_orders: list[Order] | None = None
    ) -> None:
        self._results = list(results)
        self._open_orders = open_orders or []
        self.lookup_calls = 0

    async def find_order_by_client_id(self, client_order_id: str) -> Order | None:
        idx = min(self.lookup_calls, len(self._results) - 1)
        self.lookup_calls += 1
        return self._results[idx]

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        return self._open_orders


class _SpyRiskGateRepo:
    """`RiskGateRepository`가 요구하는 것 중 `activate_safety_control()`이
    실제로 부르는 두 메서드만 최소 구현한다(`audit_repo`를 안 넘기므로
    나머지는 호출되지 않는다)."""

    def __init__(self) -> None:
        self.activated: list[SafetyControl] = []
        self.invalidated: list[UUID | None] = []

    async def insert_safety_control(
        self, *, scope: SafetyScope, scope_ref: str, reason: str, actor_subject_id: UUID
    ) -> SafetyControl:
        control = SafetyControl(
            id=uuid4(),
            scope=scope,
            scope_ref=scope_ref,
            state=SafetyControlState.ACTIVE,
            reason=reason,
            actor_subject_id=actor_subject_id,
            fence_token=1,
        )
        self.activated.append(control)
        return control

    async def invalidate_evaluations(self, *, tenant_id: UUID | None) -> None:
        self.invalidated.append(tenant_id)


async def test_risk_gate_repo_none_is_rejected_i01() -> None:
    order = _order_view()
    with pytest.raises(TypeError):
        await unknown_resolver.resolve_unknown(
            order.order_id,
            adapter=_LookupAdapter(results=[None]),
            pool=_FakePool(),
            risk_gate_repo=None,  # type: ignore[arg-type]
            clock=lambda: BASE,
            sleep=_no_sleep,
            order_repo=_FakeOrderRepo(order),
        )


async def test_already_resolved_order_short_circuits_without_adapter_call() -> None:
    order = _order_view(status=OrderStatus.ACKNOWLEDGED)
    adapter = _LookupAdapter(results=[])

    result = await unknown_resolver.resolve_unknown(
        order.order_id,
        adapter=adapter,
        pool=_FakePool(),
        risk_gate_repo=_SpyRiskGateRepo(),
        clock=lambda: BASE,
        sleep=_no_sleep,
        order_repo=_FakeOrderRepo(order),
    )

    assert result.status is OrderStatus.ACKNOWLEDGED
    assert adapter.lookup_calls == 0


async def test_missing_unknown_since_is_a_data_integrity_fail_closed() -> None:
    order = _order_view(unknown_since=None)

    with pytest.raises(ValueError):
        await unknown_resolver.resolve_unknown(
            order.order_id,
            adapter=_LookupAdapter(results=[None]),
            pool=_FakePool(),
            risk_gate_repo=_SpyRiskGateRepo(),
            clock=lambda: BASE,
            sleep=_no_sleep,
            order_repo=_FakeOrderRepo(order),
        )


async def test_found_order_with_disallowed_target_status_is_fail_closed() -> None:
    """어댑터 계약 위반(§6 F5-a `find_order_by_client_id`는 ACK 이전 상태를
    돌려주면 안 된다) — 조용히 받아들이지 않고 예외로 드러낸다."""
    order = _order_view()
    bad_found = _order(status=OrderStatus.SUBMITTED)

    with pytest.raises(InvalidOrderTransitionError):
        await unknown_resolver.resolve_unknown(
            order.order_id,
            adapter=_LookupAdapter(results=[bad_found]),
            pool=_FakePool(),
            risk_gate_repo=_SpyRiskGateRepo(),
            clock=lambda: BASE,
            sleep=_no_sleep,
            order_repo=_FakeOrderRepo(order),
            max_attempts=1,
        )


@pytest.mark.parametrize(
    "not_found_calls,elapsed_seconds,expect_absent",
    [
        (1, 200.0, False),  # 1회뿐 — 연속 2회 미달
        (2, 119.0, False),  # 2회지만 120s 미달(경계 아래)
        (2, 120.0, True),  # 정확히 120s 경계 — 통과
        (2, 200.0, True),  # 충분히 경과 + 2회
    ],
)
async def test_resolved_absent_boundary_matrix(
    not_found_calls: int, elapsed_seconds: float, expect_absent: bool
) -> None:
    order = _order_view()
    repo = _FakeOrderRepo(order)
    adapter = _LookupAdapter(results=[None] * not_found_calls)
    risk_repo = _SpyRiskGateRepo()
    fixed_now = BASE + timedelta(seconds=elapsed_seconds)

    result = await unknown_resolver.resolve_unknown(
        order.order_id,
        adapter=adapter,
        pool=_FakePool(),
        risk_gate_repo=risk_repo,
        clock=lambda: fixed_now,
        sleep=_no_sleep,
        order_repo=repo,
        max_attempts=not_found_calls,
        backoff=(0.0,) * 5,
    )

    if expect_absent:
        assert result.status is OrderStatus.FAILED
        assert repo.events == ["RESOLVED_ABSENT"]
        assert risk_repo.activated == []
    else:
        assert result.status is OrderStatus.UNKNOWN
        assert repo.events == ["UNRESOLVED_LIMIT"]
        assert len(risk_repo.activated) == 1


async def test_still_open_blocks_absent_even_past_boundary() -> None:
    order = _order_view()
    repo = _FakeOrderRepo(order)
    matching_open = _order(client_order_id=order.client_order_id, status=OrderStatus.ACKNOWLEDGED)
    adapter = _LookupAdapter(results=[None, None], open_orders=[matching_open])
    risk_repo = _SpyRiskGateRepo()

    result = await unknown_resolver.resolve_unknown(
        order.order_id,
        adapter=adapter,
        pool=_FakePool(),
        risk_gate_repo=risk_repo,
        clock=lambda: BASE + timedelta(seconds=300),
        sleep=_no_sleep,
        order_repo=repo,
        max_attempts=2,
        backoff=(0.0, 0.0),
    )

    assert result.status is OrderStatus.UNKNOWN
    assert len(risk_repo.activated) == 1


async def test_escalation_activates_account_scope_safety_control() -> None:
    order = _order_view()
    repo = _FakeOrderRepo(order)
    adapter = _LookupAdapter(results=[None] * 5)
    risk_repo = _SpyRiskGateRepo()

    result = await unknown_resolver.resolve_unknown(
        order.order_id,
        adapter=adapter,
        pool=_FakePool(),
        risk_gate_repo=risk_repo,
        clock=lambda: BASE,  # 경과 0초 — 절대 ABSENT로 못 감, 상한만 소진
        sleep=_no_sleep,
        order_repo=repo,
    )

    assert result.status is OrderStatus.UNKNOWN
    assert repo.events == ["UNRESOLVED_LIMIT"]
    assert len(risk_repo.activated) == 1
    control = risk_repo.activated[0]
    assert control.scope is SafetyScope.ACCOUNT
    assert control.scope_ref == str(order.tenant_id)
    assert adapter.lookup_calls == 5


async def test_resolved_as_lookup_match_transitions_and_stops_retrying() -> None:
    order = _order_view()
    repo = _FakeOrderRepo(order)
    found = _order(
        client_order_id=order.client_order_id,
        exchange_order_id="ex-777",
        status=OrderStatus.FILLED,
        filled_quantity=order.quantity,
    )
    adapter = _LookupAdapter(results=[None, found, None, None, None])
    risk_repo = _SpyRiskGateRepo()

    result = await unknown_resolver.resolve_unknown(
        order.order_id,
        adapter=adapter,
        pool=_FakePool(),
        risk_gate_repo=risk_repo,
        clock=lambda: BASE,
        sleep=_no_sleep,
        order_repo=repo,
        backoff=(0.0,) * 5,
    )

    assert result.status is OrderStatus.FILLED
    assert result.exchange_order_id == "ex-777"
    assert adapter.lookup_calls == 2  # 3번째 이후 결과는 소비되지 않는다
    assert risk_repo.activated == []
