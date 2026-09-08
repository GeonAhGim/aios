"""L4-30 — BITGET_SPOT_PROFILE 구조 + BitgetAdapter.venue_profile() 배선.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-E, §9 L4-30

네트워크 없는 순수/구조 검증만 — 실키 왕복은
tests/integration/exchanges/bitget/test_live_demo_roundtrip.py(`live_demo`
마커)의 책임이다.
"""
from __future__ import annotations

from decimal import Decimal

import httpx

from src.data.models.base import AssetClass
from src.data.models.trading import OrderType
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.venue_profile import BITGET_SPOT_PROFILE
from src.exchanges.common.adapter import ExchangeAdapter


def _make_adapter() -> BitgetAdapter:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def test_profile_declares_bitget_spot_capabilities() -> None:
    assert BITGET_SPOT_PROFILE.venue == "bitget"
    assert BITGET_SPOT_PROFILE.asset_classes == [AssetClass.CRYPTO]
    assert BITGET_SPOT_PROFILE.order_types == {OrderType.MARKET, OrderType.LIMIT}
    assert BITGET_SPOT_PROFILE.price_tick["BTC/USDT"] == Decimal("0.01")
    assert BITGET_SPOT_PROFILE.qty_lot["BTC/USDT"] == Decimal("0.000001")
    assert BITGET_SPOT_PROFILE.min_notional["BTC/USDT"] == Decimal("1")


def test_verified_is_not_live_until_demo_roundtrip_confirms_it() -> None:
    """§10 정직 표기 — place/cancel/get 왕복이 실제로 통과하기 전까지
    `"LIVE_VERIFIED"`를 자칭하지 않는다(ADR-2026-09-06-G §11)."""
    assert BITGET_SPOT_PROFILE.verified in ("DOC_ONLY", "ESTIMATED")


def test_adapter_venue_profile_returns_the_bitget_constant() -> None:
    """negative — ABC 기본 구현(`UnsupportedCapabilityError`)이 아니라
    이 어댑터의 실제 override로 해석돼야 한다(MRO 역전 방지, 기존
    test_adapter_abc_defaults.py와 동일한 패턴)."""
    adapter = _make_adapter()
    assert adapter.venue_profile() is BITGET_SPOT_PROFILE
    assert BitgetAdapter.venue_profile is not ExchangeAdapter.venue_profile
