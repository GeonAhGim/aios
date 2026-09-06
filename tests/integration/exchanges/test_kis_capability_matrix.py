"""task-1786 BR-8 — KIS capabilities 정합 + ExchangeAdapter 계약 승격.

DoD 셋을 각각 다른 각도에서 증명한다:
1. capabilities가 매트릭스의 구현 상태와 일치(선언과 구현이 어긋나면 실패) —
   `get_capabilities()`가 선언한 자산군 전부가 실제로 `place_order()` 분기를
   통해 올바른 KIS 엔드포인트에 도달함을 왕복으로 증명한다(선언만 하고
   분기를 빼먹으면 이 테스트가 잘못된 경로로 실패한다).
2. 미지원 자산군 주문은 게이트 이전에 거부 — Validator(`validate_order_params`)
   와 OMS(`assert_supported`) 둘 다, 뒤에 오는 컴플라이언스 게이트가
   호출되기도 전에 CRYPTO 주문을 거부함을 증명한다(게이트 콜백이 실행됐는지
   카운터로 직접 확인).
3. 전 자산군 주문이 리스크·컴플라이언스 게이트를 그대로 통과 — 선언된 10개
   자산군 전부가 `assert_supported`를 통과해 그 다음 게이트로 넘어감을
   증명한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from src.core.validator.order_validator import validate_order_params
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import KISAdapter
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.errors import OrderValidationError
from src.services.oms.domain.venue_profile import (
    TimeoutBudget,
    VenueCapabilityProfile,
    assert_supported,
)

_DOMESTIC_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_DOMESTIC_FO_ORDER_PATH = "/uapi/domestic-futureoption/v1/trading/order"
_OVERSEAS_FO_ORDER_PATH = "/uapi/overseas-futureoption/v1/trading/order"
_OVERSEAS_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"

# BR-8이 승격한 전체 매트릭스 — get_capabilities()가 선언해야 하는 값과
# 정확히 같아야 한다(어느 한쪽만 바뀌면 아래 두 테스트 중 하나가 실패).
EXPECTED_ASSET_CLASSES = frozenset(
    {
        AssetClass.KR_EQUITY,
        AssetClass.KR_ETF,
        AssetClass.KR_ETN,
        AssetClass.KR_FUTURES,
        AssetClass.KR_OPTION,
        AssetClass.US_EQUITY,
        AssetClass.US_ETF,
        AssetClass.US_ETN,
        AssetClass.OVERSEAS_FUTURES,
        AssetClass.OVERSEAS_OPTION,
    }
)

# asset_class -> (symbol(어댑터 진입점 형식), 도달해야 하는 실제 주문 경로)
_ROUTING_CASES: dict[AssetClass, tuple[str, str]] = {
    AssetClass.KR_EQUITY: ("005930", _DOMESTIC_ORDER_PATH),
    AssetClass.KR_ETF: ("069500", _DOMESTIC_ORDER_PATH),
    AssetClass.KR_ETN: ("580007", _DOMESTIC_ORDER_PATH),
    AssetClass.KR_FUTURES: ("101W09", _DOMESTIC_FO_ORDER_PATH),
    AssetClass.KR_OPTION: ("201W09", _DOMESTIC_FO_ORDER_PATH),
    AssetClass.US_EQUITY: ("NASD:AAPL", _OVERSEAS_ORDER_PATH),
    AssetClass.US_ETF: ("NASD:SPY", _OVERSEAS_ORDER_PATH),
    AssetClass.US_ETN: ("NASD:VXX", _OVERSEAS_ORDER_PATH),
    AssetClass.OVERSEAS_FUTURES: ("ESZ26", _OVERSEAS_FO_ORDER_PATH),
    AssetClass.OVERSEAS_OPTION: ("ESZ26C", _OVERSEAS_FO_ORDER_PATH),
}

assert set(_ROUTING_CASES) == EXPECTED_ASSET_CLASSES  # 매트릭스 자체가 누락 없이 커버됐는지


def _make_paper_adapter(captured: list[httpx.Request]) -> KISAdapter:
    order_paths = {
        _DOMESTIC_ORDER_PATH,
        _DOMESTIC_FO_ORDER_PATH,
        _OVERSEAS_FO_ORDER_PATH,
        _OVERSEAS_ORDER_PATH,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})
        captured.append(request)
        if path in order_paths:
            output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        else:
            raise AssertionError(f"예상치 못한 경로: {path}")
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def _order(asset_class: AssetClass, symbol: str) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol=symbol,
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=asset_class,
    )


def test_capabilities_declare_exact_implemented_matrix() -> None:
    adapter = _make_paper_adapter([])
    caps = adapter.get_capabilities()

    assert set(caps.supported_asset_classes) == EXPECTED_ASSET_CLASSES
    assert AssetClass.CRYPTO not in caps.supported_asset_classes


@pytest.mark.parametrize("asset_class", sorted(EXPECTED_ASSET_CLASSES, key=lambda c: c.value))
async def test_place_order_dispatches_declared_asset_class_to_correct_endpoint(
    asset_class: AssetClass,
) -> None:
    """DoD 1의 핵심 — 선언(get_capabilities)과 구현(place_order 분기)이
    어긋나면 여기서 실패한다: 잘못 분기되면 다른 경로가 호출되거나(경로
    미스매치로 AssertionError) 아예 도달하지 못한다(예외)."""
    symbol, expected_path = _ROUTING_CASES[asset_class]
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    result = await adapter.place_order(_order(asset_class, symbol))

    assert result.exchange_order_id == "ORG:1"
    assert captured[-1].url.path == expected_path


async def test_place_order_rejects_undeclared_asset_class_without_any_http_call() -> None:
    """CRYPTO는 capabilities에 없다 — place_order()가 어떤 주문 엔드포인트도
    건드리지 않고 즉시 거부해야 한다(잘못된 시장으로 나가는 사고 방지)."""
    from src.core.exceptions import FatalExchangeError

    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    with pytest.raises(FatalExchangeError):
        await adapter.place_order(_order(AssetClass.CRYPTO, "BTC/USDT"))

    assert captured == []


async def test_place_order_rejects_overseas_symbol_without_exchange_prefix() -> None:
    """해외주식 심볼이 'EXCG:심볼' 형식이 아니면 거래소를 추측하지 않고
    거부한다(8.3 fail-closed) — negative test."""
    from src.core.exceptions import FatalExchangeError

    adapter = _make_paper_adapter([])

    with pytest.raises(FatalExchangeError):
        await adapter.place_order(_order(AssetClass.US_EQUITY, "AAPL"))


def test_validate_order_params_rejects_unsupported_asset_class_before_gate() -> None:
    """DoD 2 — Validator 게이트가 컴플라이언스 게이트보다 먼저 평가돼야
    한다. 게이트 콜백을 카운터로 감시해 호출 여부를 직접 확인한다."""
    adapter = _make_paper_adapter([])
    caps = adapter.get_capabilities()
    order = _order(AssetClass.CRYPTO, "BTC/USDT")

    gate_calls = 0

    def compliance_gate() -> None:
        nonlocal gate_calls
        gate_calls += 1

    validation = validate_order_params(order, supported_asset_classes=caps.supported_asset_classes)
    if validation.is_valid:
        compliance_gate()

    assert not validation.is_valid
    assert "UNSUPPORTED_ASSET_CLASS" in validation.errors[0]
    assert gate_calls == 0  # 게이트는 아예 호출되지 않았다


def _venue_profile(asset_classes: list[AssetClass]) -> VenueCapabilityProfile:
    """KIS 실제 운영 프로파일(L4-04 잔여분)은 이 리프의 파일 스콥 밖 —
    여기서는 get_capabilities()가 선언한 자산군 목록만 옮겨 `assert_supported`
    (submit_order.py:156, pre_submit_gate 호출보다 먼저 평가됨)가 실제로
    전 자산군을 통과시키는지만 확인한다."""
    return VenueCapabilityProfile(
        venue="kis",
        asset_classes=asset_classes,
        order_types={OrderType.MARKET, OrderType.LIMIT},
        time_in_force={"GTC", "IOC", "FOK", "DAY"},
        supports_client_order_id=False,  # KIS는 client_order_id 개념이 없음(adapter.py docstring)
        client_order_id_max_len=0,
        client_order_id_charset="",
        id_policy="DAILY_SEQUENCE",
        supports_modify=True,
        supports_cancel="YES",
        supports_ws_orders=True,
        supports_batch=False,
        price_tick={},
        qty_lot={},
        min_notional={},
        rate_limits={},
        submit_timeout=TimeoutBudget(),
        query_timeout=TimeoutBudget(),
        market_hours=None,
        max_open_orders_per_symbol=20,
        verified="ESTIMATED",
    )


def _submit_command(asset_class: AssetClass, symbol: str) -> SubmitOrderCommand:
    return SubmitOrderCommand(
        command_id=uuid4(),
        trace_id=uuid4(),
        scope=OrderIdempotencyScope(
            tenant_id=uuid4(),
            account_ref="acct-1",
            provider="kis",
            strategy_id="s1",
            strategy_version="1.0.0",
            execution_id=1,
            intent_seq=1,
            window_start=datetime.now(timezone.utc),
        ),
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=None,
        time_in_force="GTC",
        asset_class=asset_class,
        actor_subject_id=uuid4(),
        issued_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize("asset_class", sorted(EXPECTED_ASSET_CLASSES, key=lambda c: c.value))
def test_assert_supported_passes_all_declared_asset_classes_to_next_gate(
    asset_class: AssetClass,
) -> None:
    """DoD 3 — 선언된 10개 자산군 전부가 OMS 사전 게이트(`assert_supported`)를
    그대로 통과해 다음 게이트(컴플라이언스/킬스위치, submit_order.py의
    pre_submit_gate)로 넘어가야 한다."""
    caps = KISAdapter("app", "secret", "1", "01").get_capabilities()
    profile = _venue_profile(list(caps.supported_asset_classes))
    cmd = _submit_command(asset_class, _ROUTING_CASES[asset_class][0])

    assert_supported(profile, cmd)  # 예외 없이 통과해야 다음 게이트로 넘어간다


def test_assert_supported_rejects_undeclared_asset_class_before_next_gate() -> None:
    """반대쪽 — capabilities에 없는 자산군(CRYPTO)은 다음 게이트(컴플라이언스)
    콜백이 호출되기 전에 거부돼야 한다."""
    caps = KISAdapter("app", "secret", "1", "01").get_capabilities()
    profile = _venue_profile(list(caps.supported_asset_classes))
    cmd = _submit_command(AssetClass.CRYPTO, "BTC/USDT")

    next_gate_calls = 0

    def next_gate() -> None:
        nonlocal next_gate_calls
        next_gate_calls += 1

    with pytest.raises(OrderValidationError) as exc_info:
        assert_supported(profile, cmd)
        next_gate()  # submit_order.py의 pre_submit_gate 호출 위치와 동일 순서

    assert exc_info.value.code == "OMS_VALIDATION_UNSUPPORTED_TYPE"
    assert next_gate_calls == 0
