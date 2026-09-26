"""task-7598(BR-21d) — OKXTradingMixin 주문·취소·정정 테스트.

아직 OKXAdapter 조립체(task BR-21b auth.py/factory.py, BR-21c
account_mixin.py)가 없으므로, KIS/Bitget/Kiwoom trading_mixin 테스트와
동일한 관례로 믹스인 + 최소 스텁 클라이언트를 이 파일 안에서 직접
구성한다(D2 floor: 부정 테스트 ≥3, 장애주입 1, 성능 수치 단언 1, D3:
INVARIANTS I-02/I-03 대조 적대적 테스트 1 — 근거는 각 테스트
docstring/모듈 하단 참고).

task-7868(BR-21 정정, 리뷰 REJECT 7802) — `order.symbol`은 canonical
"BASE/QUOTE"(예: "BTC/USDT")다. 아래 `_order()`는 이제 canonical을 쓰고,
`place_order`가 그것을 OKX `instId`("BASE-QUOTE")로 변환해 전송하는지
검증한다. OKX raw 형식("BTC-USDT")을 직접 넣는 옛 계약은 거부로
확정했다(정규화 대신 명시적 실패 — `test_place_order_rejects_non_canonical_symbol`).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import (
    ExchangeAPIError,
    FatalExchangeError,
    FrozenZonePaperAdapterBlockedError,
)
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.market_data import Ticker
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.okx.trading_mixin import OKXTradingMixin, _to_okx_ord_type

pytestmark = pytest.mark.asyncio


class _StubClient(OKXTradingMixin):
    def __init__(
        self,
        *,
        demo_mode: bool,
        responses: dict[str, dict[str, Any]] | None = None,
        raise_path: str | None = None,
        ticker_price: Decimal | None = Decimal("50000"),
        ticker_error: Exception | None = None,
        ticker_age: timedelta | None = None,
    ) -> None:
        self.is_paper_trading = demo_mode
        self.is_sandboxed = demo_mode
        self._responses = responses or {}
        self._raise_path = raise_path
        self._ticker_price = ticker_price
        self._ticker_error = ticker_error
        self._ticker_age = ticker_age if ticker_age is not None else timedelta(seconds=0)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.ticker_calls: list[str] = []

    async def _request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if self._raise_path == path:
            raise AssertionError(f"가드가 막았어야 할 요청이 실제로 나갔습니다: {path}")
        return self._responses.get(
            path, {"code": "0", "msg": "", "data": [{"ordId": "OID-1", "sCode": "0", "sMsg": ""}]}
        )

    async def get_order(self, order_id: str) -> Order:
        return _order().model_copy(
            update={"exchange_order_id": order_id, "status": OrderStatus.ACKNOWLEDGED}
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        self.ticker_calls.append(symbol)
        if self._ticker_error is not None:
            raise self._ticker_error
        assert self._ticker_price is not None
        return Ticker(
            symbol=symbol,
            exchange="okx",
            price=self._ticker_price,
            bid=self._ticker_price,
            ask=self._ticker_price,
            volume_24h=Decimal("0"),
            timestamp=datetime.now(timezone.utc) - self._ticker_age,
            source_type="reference",
        )


def _order(*, side: OrderSide = OrderSide.BUY, order_type: OrderType = OrderType.LIMIT) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="okx",
        side=side,
        order_type=order_type,
        quantity=Decimal("1"),
        price=None
        if order_type == OrderType.MARKET
        else Money(amount=Decimal("50000"), currency=Currency.USDT),
        asset_class=AssetClass.CRYPTO,
    )


def _live_client(raise_path: str | None = None) -> _StubClient:
    return _StubClient(demo_mode=False, raise_path=raise_path)


def _paper_client(**kwargs: Any) -> _StubClient:
    return _StubClient(demo_mode=True, **kwargs)


def _ok_order_response(ord_id: str = "1234567") -> dict[str, Any]:
    """반복되는 "code=0, 성공 data 행 1개" OKX 응답 스텁 -- 각 테스트에서
    5줄짜리 dict 리터럴을 반복하지 않기 위한 헬퍼(LOC 절감, 동작 무변화)."""
    return {"code": "0", "msg": "", "data": [{"ordId": ord_id, "sCode": "0", "sMsg": ""}]}


# ---- LIVE 하드가드 (D3 — INVARIANTS I-02/I-03 대조 적대적 테스트) ----


async def test_place_order_rejects_live_adapter():
    """demo_mode=False(LIVE)에서는 place_order가 HTTP 요청 전에 거부돼야
    한다 — 요청이 실제로 나가면 _request가 AssertionError로 실패시킨다.
    이 요구사항 재확인: factory.py의 AIOS_ALLOW_LIVE_ADAPTER 생성시점
    가드와는 별도의 방어선(require_paper_sandbox, 메서드 호출시점)이며,
    아래는 OKX 어댑터에도 이 방어선이 예외 없이 적용됨을 증명한다 —
    우회 경로 없음: calls == []."""
    client = _live_client(raise_path="/api/v5/trade/order")
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await client.place_order(_order())
    assert client.calls == []


async def test_cancel_order_rejects_live_adapter():
    client = _live_client(raise_path="/api/v5/trade/cancel-order")
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await client.cancel_order("BTC-USDT:OID-1")
    assert client.calls == []


async def test_modify_order_rejects_live_adapter():
    client = _live_client(raise_path="/api/v5/trade/amend-order")
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await client.modify_order("BTC-USDT:OID-1", price=Decimal("51000"))
    assert client.calls == []


# ---- 해피 패스 ----


async def test_place_order_buy_limit_builds_composite_exchange_order_id():
    client = _paper_client(responses={"/api/v5/trade/order": _ok_order_response()})
    result = await client.place_order(_order(side=OrderSide.BUY, order_type=OrderType.LIMIT))
    assert result.exchange_order_id == "BTC-USDT:1234567"
    assert result.status == OrderStatus.SUBMITTED
    _, path, body = client.calls[0]
    assert path == "/api/v5/trade/order"
    assert body is not None
    assert body["instId"] == "BTC-USDT"
    assert body["tdMode"] == "cash"
    assert body["side"] == "buy"
    assert body["ordType"] == "limit"
    assert body["px"] == "50000"
    assert body["sz"] == "1"


async def test_place_order_sell_market_omits_price_field():
    client = _paper_client(responses={"/api/v5/trade/order": _ok_order_response("7654321")})
    result = await client.place_order(_order(side=OrderSide.SELL, order_type=OrderType.MARKET))
    _, path, body = client.calls[0]
    assert path == "/api/v5/trade/order"
    assert body["side"] == "sell"
    assert body["ordType"] == "market"
    assert "px" not in body
    assert result.exchange_order_id == "BTC-USDT:7654321"


async def test_cancel_order_splits_composite_id_and_returns_true_on_success():
    client = _paper_client(responses={"/api/v5/trade/cancel-order": _ok_order_response()})
    ok = await client.cancel_order("BTC-USDT:1234567")
    assert ok is True
    _, path, body = client.calls[0]
    assert path == "/api/v5/trade/cancel-order"
    assert body["instId"] == "BTC-USDT"
    assert body["ordId"] == "1234567"


async def test_modify_order_accepts_price_and_delegates_to_get_order():
    client = _paper_client(responses={"/api/v5/trade/amend-order": _ok_order_response()})
    result = await client.modify_order(
        "BTC-USDT:1234567", price=Decimal("51000"), size=Decimal("2")
    )
    _, path, body = client.calls[0]
    assert path == "/api/v5/trade/amend-order"
    assert body["newPx"] == "51000"
    assert body["newSz"] == "2"
    assert result.exchange_order_id == "BTC-USDT:1234567"


# ---- 부정/장애주입 테스트 (D2 floor ≥3 부정 + 1 장애주입) ----


async def test_place_order_rejects_non_positive_quantity():
    """부정 테스트 1(잘못된 수량): 0 이하 수량은 거래소 호출 전에 거부."""
    client = _paper_client()
    bad_order = _order().model_copy(update={"quantity": Decimal("0")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(bad_order)
    assert client.calls == []


async def test_place_order_rejects_limit_order_without_price():
    """부정 테스트 2(잘못된 가격): LIMIT 주문에 유효한 가격이 없으면
    거래소 호출 전에 거부(OKX limit ordType은 px가 필수)."""
    client = _paper_client()
    bad_order = _order(order_type=OrderType.LIMIT).model_copy(update={"price": None})
    with pytest.raises(FatalExchangeError):
        await client.place_order(bad_order)
    assert client.calls == []


async def test_to_okx_ord_type_rejects_unsupported_order_type():
    """부정 테스트 3(미지원 주문타입): 도메인 OrderType은 MARKET/LIMIT만
    갖지만(src/data/models/trading.py), 매핑 함수 자체는 그 밖의 값이
    들어오면 추측 대신 fail-closed로 거부해야 한다(모듈 docstring
    "Unverified scope" 참조) — post_only/fok/ioc 등 OKX 전용 타입을
    조용히 잘못 매핑하지 않는지 직접 검증."""
    with pytest.raises(FatalExchangeError):
        _to_okx_ord_type("post_only")  # type: ignore[arg-type]


async def test_modify_order_rejects_when_neither_price_nor_size_given():
    """부정 테스트 4: OKX amend-order는 newSz/newPx 중 최소 하나가
    필요함 — 둘 다 없으면 거래소에 요청을 보내기 전에 거부한다."""
    client = _paper_client()
    with pytest.raises(FatalExchangeError):
        await client.modify_order("BTC-USDT:1234567")
    assert client.calls == []


async def test_cancel_order_rejects_malformed_exchange_order_id():
    """부정 테스트 5: ':' 구분자가 없는 exchange_order_id는 FatalExchangeError."""
    client = _paper_client()
    with pytest.raises(FatalExchangeError):
        await client.cancel_order("not-a-composite-id")
    assert client.calls == []


async def test_place_order_rejects_tick_misaligned_price():
    """task-8076(F4-OKX, DoD 1): 지정가 가격이 BTC/USDT tick size(0.1)에
    정렬되지 않으면 거래소 호출 전에 거부한다."""
    client = _paper_client()
    bad_order = _order(order_type=OrderType.LIMIT).model_copy(
        update={"price": Money(amount=Decimal("50000.05"), currency=Currency.USDT)}
    )
    with pytest.raises(FatalExchangeError):
        await client.place_order(bad_order)
    assert client.calls == []


async def test_place_order_rejects_lot_misaligned_quantity():
    """task-8076(F4-OKX): 수량이 BTC/USDT lot size(0.00000001)에 정렬되지
    않으면 거래소 호출 전에 거부한다."""
    client = _paper_client()
    bad_order = _order().model_copy(update={"quantity": Decimal("1.000000001")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(bad_order)
    assert client.calls == []


async def test_place_order_rejects_below_min_notional():
    """task-8076(F4-OKX, DoD 2): 가격*수량이 BTC/USDT min_notional(1)
    미만이면 거래소 호출 전에 거부한다."""
    client = _paper_client()
    bad_order = _order(order_type=OrderType.LIMIT).model_copy(
        update={
            "price": Money(amount=Decimal("0.1"), currency=Currency.USDT),
            "quantity": Decimal("1"),
        }
    )
    with pytest.raises(FatalExchangeError):
        await client.place_order(bad_order)
    assert client.calls == []


async def test_place_order_rejects_below_min_notional_market_order():
    """task-8336(리뷰 REJECT task-8179 후속), task-8357(CTO 결정으로 재작성):
    MARKET 주문은 price가 없어 기존 가드(order_type==LIMIT 조건)가 완전히
    스킵됐었다 -- 이제 참조가(get_ticker)*quantity로 실제 notional을 계산해
    BTC/USDT min_notional(1) 미달이면 거래소 호출 전에 거부한다. 참조가
    50000일 때 quantity=0.00001 -> notional=0.5 < 1."""
    client = _paper_client(ticker_price=Decimal("50000"))
    bad_order = _order(order_type=OrderType.MARKET).model_copy(
        update={"quantity": Decimal("0.00001")}
    )
    with pytest.raises(FatalExchangeError):
        await client.place_order(bad_order)
    assert client.calls == []
    assert client.ticker_calls == ["BTC/USDT"]


async def test_place_order_rejects_finding_223_large_quantity_low_real_notional():
    """finding#223(QA task-8357) 재현: task-8336의 절대값 비교
    (quantity>=min_notional)는 quantity=1(=min_notional)이면 무조건 통과시켜,
    실제 notional이 낮은 저가 코인 대량 주문을 놓쳤다. 참조가 0.5일 때
    quantity=1 -> 실제 notional=0.5 < min_notional(1)이므로 이제는 거부돼야
    한다 -- 절대값 비교였다면 이 케이스는 (구현 결함으로) 통과했었다."""
    client = _paper_client(ticker_price=Decimal("0.5"))
    bad_order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("1")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(bad_order)
    assert client.calls == []


async def test_place_order_accepts_small_quantity_market_order_with_sufficient_real_notional():
    """finding#223(QA task-8357) 재현: task-8336의 절대값 비교는 quantity가
    min_notional(1)보다 작으면(예: 0.1 BTC) 실제 notional이 충분해도 거부해,
    검증 목적과 반대로 동작했다. 참조가 50000일 때 quantity=0.1 ->
    실제 notional=5000 >= min_notional(1)이므로 이제는 통과해야 한다."""
    client = _paper_client(
        ticker_price=Decimal("50000"),
        responses={"/api/v5/trade/order": _ok_order_response()},
    )
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("0.1")})
    result = await client.place_order(order)
    assert result.exchange_order_id == "BTC-USDT:1234567"


async def test_place_order_accepts_market_order_at_min_notional_boundary():
    """회귀 방지: 참조가*quantity가 min_notional(1)과 정확히 같은 경계값은
    통과한다(엄격한 `<` 비교, `<=` 아님) -- 참조가 1, quantity 1 -> notional 1."""
    client = _paper_client(
        ticker_price=Decimal("1"),
        responses={"/api/v5/trade/order": _ok_order_response()},
    )
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("1")})
    result = await client.place_order(order)
    assert result.exchange_order_id == "BTC-USDT:1234567"


async def test_place_order_rejects_market_order_when_reference_price_fetch_fails():
    """부정 테스트 + 장애주입(task-8357, CTO 결정): 참조가 조회
    (get_ticker)가 실패하면 근사치로 되돌아가거나 검증을 건너뛰지 않고
    fail-closed로 주문 자체를 거부해야 한다."""
    client = _paper_client(ticker_error=ExchangeAPIError("price feed 장애"))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("1")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(order)
    assert client.calls == []


async def test_place_order_rejects_market_order_with_stale_reference_price():
    """부정 테스트(task-8374, finding#223 후속): 5초 초과로 스테일한
    참조가는 notional이 충분해도(50000*0.1=5000) 신뢰하지 않고 거부."""
    client = _paper_client(ticker_price=Decimal("50000"), ticker_age=timedelta(seconds=6))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("0.1")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(order)
    assert client.calls == []


async def test_place_order_rejects_market_order_with_zero_reference_price():
    """부정 테스트(task-8374): 참조가 0은 notional을 무조건 0으로 만들어
    min_notional 검증을 무의미하게 통과시키므로 계산 전에 거부."""
    client = _paper_client(ticker_price=Decimal("0"))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("1")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(order)
    assert client.calls == []


async def test_place_order_rejects_market_order_with_negative_reference_price():
    """부정 테스트(task-8374): 음수 참조가(데이터 소스 이상)도 거부 --
    0 검사만으로는 걸러지지 않는 별도 경로."""
    client = _paper_client(ticker_price=Decimal("-1"))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("1")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(order)
    assert client.calls == []


async def test_place_order_accepts_market_order_with_reference_price_just_under_staleness_limit():
    """회귀 방지(QA task-8433): 스테일 거부(6초) 테스트만 있고 경계 통과가
    없었다 -- 임계값(5초) 바로 아래(4.9초, 정각은 실행시간 오차로 피함)는
    통과함을 증명한다."""
    client = _paper_client(
        ticker_price=Decimal("50000"),
        ticker_age=timedelta(milliseconds=4900),
        responses={"/api/v5/trade/order": _ok_order_response()},
    )
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("0.1")})
    result = await client.place_order(order)
    assert result.exchange_order_id == "BTC-USDT:1234567"


async def test_place_order_rejects_symbol_without_registered_limits():
    """task-8076(F4-OKX): tick/lot/min_notional 한도가 없는 심볼은 검증을
    건너뛰지 않고 fail-closed로 거부한다."""
    client = _paper_client()
    bad_order = _order().model_copy(update={"symbol": "SOL/USDT"})
    with pytest.raises(FatalExchangeError):
        await client.place_order(bad_order)
    assert client.calls == []


async def test_place_order_rejects_non_canonical_symbol():
    """부정 테스트(task-7868, 리뷰 REJECT 7802): `order.symbol`이 이미 OKX
    raw 형식("BTC-USDT", "/" 없음)이면 canonical 파서가 구분자를 찾지 못해
    거래소 호출 전에 FatalExchangeError로 거부한다 — 정규화 대신 명시적
    거부(계약)."""
    client = _paper_client()
    bad_order = _order().model_copy(update={"symbol": "BTC-USDT"})
    with pytest.raises(FatalExchangeError):
        await client.place_order(bad_order)
    assert client.calls == []


async def test_place_order_raises_fatal_exchange_error_on_order_level_failure():
    """부정 테스트 6 + 장애주입: OKX는 배치 레벨(code)과 주문 레벨(sCode)
    실패를 분리해서 보고한다 — code=="0"(배치 통과)이어도 개별 주문의
    sCode!="0"이면 그 주문은 실패한 것이므로, 조용히 성공 처리하지 않고
    FatalExchangeError로 통일해 호출부가 원인을 알 수 있게 한다."""
    client = _paper_client(
        responses={
            "/api/v5/trade/order": {
                "code": "0",
                "msg": "",
                "data": [{"ordId": "", "sCode": "51008", "sMsg": "Order failed"}],
            }
        }
    )
    with pytest.raises(FatalExchangeError):
        await client.place_order(_order())


async def test_place_order_raises_fatal_exchange_error_on_empty_data_array():
    """부정 테스트 7 + 장애주입: 응답 스키마 이상(data 빈 배열)도
    IndexError 대신 FatalExchangeError로 통일."""
    client = _paper_client(responses={"/api/v5/trade/order": {"code": "0", "msg": "", "data": []}})
    with pytest.raises(FatalExchangeError):
        await client.place_order(_order())


# ---- 성능 수치 단언 ----


@pytest.mark.perf
async def test_place_order_latency_budget():
    """숫자 성능 단언: place_order는 순수 스텁 클라이언트(네트워크 없음)
    기준 100회 호출 평균 1ms 미만이어야 한다 — 실제 거래소 왕복 시간이
    아니라, 믹스인 자체의 오버헤드(바디 조립·응답 파싱)에 대한 회귀
    가드다. 실거래소 p95/p99 레이턴시 예산은 조립체(adapter.py, 미구현)
    단계에서 계약 테스트로 별도 측정한다 — N/A(HTTP 클라이언트 미존재,
    task BR-21b 선행 필요)."""
    client = _paper_client(responses={"/api/v5/trade/order": _ok_order_response()})
    order = _order()
    start = time.perf_counter()
    for _ in range(100):
        await client.place_order(order)
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.001
