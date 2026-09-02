"""LiveReadonlyAccountProvider 단위테스트 — 실 네트워크/DB 없이, 거래소
어댑터를 스텁으로 대체한다.

전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §6) 배정 — 운영 DI가
FakeReadonlyAccountProvider만 반환하던 갭을 메운 실 어댑터."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import AccountBalance, Position
from src.foundation.connections.adapters.live_provider import LiveReadonlyAccountProvider
from src.foundation.connections.domain.models import CapabilityScope
from src.foundation.connections.ports.provider import OpaqueRef, SecretLease


class _StubAdapter:
    def __init__(self, balances: list[AccountBalance], positions: list[Position]) -> None:
        self._balances = balances
        self._positions = positions
        self.get_balance_called = False

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        self.get_balance_called = True
        return self._balances

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        return self._positions


class _StubResolver:
    """`CredentialResolver`와 같은 `get_adapter(user_id, exchange)` 시그니처만
    흉내낸다 — LiveReadonlyAccountProvider는 duck-typing으로 호출한다."""

    def __init__(self, adapter: _StubAdapter) -> None:
        self._adapter = adapter
        self.requested: tuple[object, str] | None = None

    async def get_adapter(self, user_id, exchange: str):
        self.requested = (user_id, exchange)
        return self._adapter


def _position(symbol: str, quantity: Decimal) -> Position:
    return Position(
        symbol=symbol,
        exchange="bitget",
        strategy_id="",
        quantity=quantity,
        average_entry_price=Money(amount=Decimal("100"), currency=Currency.USDT),
        current_price=Money(amount=Decimal("100"), currency=Currency.USDT),
        unrealized_pnl=Money(amount=Decimal("0"), currency=Currency.USDT),
        realized_pnl=Money(amount=Decimal("0"), currency=Currency.USDT),
        entry_time=datetime.now(timezone.utc),
        asset_class=AssetClass.CRYPTO,
    )


async def test_fetch_snapshot_maps_balances_to_values():
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000.5"), available=Decimal("900")
        ),
        AccountBalance(
            exchange="bitget", asset="BTC", total=Decimal("0.05"), available=Decimal("0.05")
        ),
    ]
    resolver = _StubResolver(_StubAdapter(balances, []))
    user_id = uuid4()
    provider = LiveReadonlyAccountProvider(
        resolver,
        user_id=user_id,
        exchange="bitget",
        requested_capability_profile=(CapabilityScope.READ_BALANCE,),
    )

    snapshot = await provider.fetch_snapshot(OpaqueRef("ACCT-1"), datetime.now(timezone.utc))

    assert resolver.requested == (user_id, "bitget")
    value_by_key = {v.entity_key: v for v in snapshot.values}
    assert value_by_key["USDT"].entity_type == "BALANCE"
    assert value_by_key["USDT"].value == Decimal("1000.5")
    assert value_by_key["BTC"].value == Decimal("0.05")
    assert snapshot.currency == "USDT"


async def test_fetch_snapshot_merges_positions_without_special_casing():
    """get_positions()가 언젠가 실제 값을 채우기 시작해도(현재 Bitget/KIS는
    항상 빈 리스트) 이 어댑터는 분기 없이 그대로 병합한다."""
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("500"), available=Decimal("500")
        )
    ]
    positions = [_position("BTCUSDT", Decimal("1.25"))]
    resolver = _StubResolver(_StubAdapter(balances, positions))
    provider = LiveReadonlyAccountProvider(
        resolver,
        user_id=uuid4(),
        exchange="bitget",
        requested_capability_profile=(CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION),
    )

    snapshot = await provider.fetch_snapshot(OpaqueRef("ACCT-1"), datetime.now(timezone.utc))

    value_by_key = {v.entity_key: v for v in snapshot.values}
    assert value_by_key["BTCUSDT"].entity_type == "POSITION"
    assert value_by_key["BTCUSDT"].value == Decimal("1.25")
    assert value_by_key["USDT"].entity_type == "BALANCE"


async def test_fetch_snapshot_with_no_balances_uses_placeholder_currency():
    resolver = _StubResolver(_StubAdapter([], []))
    provider = LiveReadonlyAccountProvider(
        resolver, user_id=uuid4(), exchange="bitget", requested_capability_profile=()
    )

    snapshot = await provider.fetch_snapshot(OpaqueRef("ACCT-1"), datetime.now(timezone.utc))

    assert snapshot.values == ()
    assert snapshot.currency == "MULTI"


async def test_verify_readonly_scope_is_honestly_unverified():
    """전수감사 지시 — 거래소 API에 AIOS 권한 taxonomy로 매핑되는 조회
    수단이 없으니 요청 스코프를 그대로 승인된 것으로 다루되
    provider_verified=False로 정직하게 표기한다."""
    resolver = _StubResolver(_StubAdapter([AccountBalance(
        exchange="bitget", asset="USDT", total=Decimal("1"), available=Decimal("1")
    )], []))
    requested = (CapabilityScope.READ_BALANCE, CapabilityScope.READ_POSITION)
    provider = LiveReadonlyAccountProvider(
        resolver, user_id=uuid4(), exchange="bitget", requested_capability_profile=requested
    )

    proof = await provider.verify_readonly_scope(SecretLease(lease_ref="lease-1"))

    assert proof.granted_scopes == requested
    assert proof.provider_verified is False
    assert resolver._adapter.get_balance_called is True  # 실제로 키가 동작하는지 확인했다


async def test_verify_readonly_scope_propagates_bad_credential_error():
    class _FailingAdapter(_StubAdapter):
        async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
            raise ConnectionError("bad api key")

    resolver = _StubResolver(_FailingAdapter([], []))
    provider = LiveReadonlyAccountProvider(
        resolver, user_id=uuid4(), exchange="bitget", requested_capability_profile=()
    )

    with pytest.raises(ConnectionError):
        await provider.verify_readonly_scope(SecretLease(lease_ref="lease-1"))
