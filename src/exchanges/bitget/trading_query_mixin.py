"""L4-13 — BitgetAdapter 주문 **조회** 메서드군(read-only, 자금 이동 없음).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-B(trading_mixin.py 행), §9 L4-13
      02b_bitget_api_v2_full_spec_v1.md#§3.2

task-1519 — `trading_mixin.py`가 300줄 캡(task-1032 분할 후)에 닿아 조회
전용 메서드를 이 파일로 분리했다(task note: "넘치면 조회 전용 믹스인으로
분리"). `BitgetTradingMixin`이 이 클래스를 상속하므로 `BitgetAdapter`의
베이스 목록은 그대로다(순수 이동 + 신규 2종, 기존 동작 변경 0).

엔드포인트(2026-08-28/09-02 문서 조사 — 실제 응답은 Demo API 키로 라이브
검증 필요, 04번 §11.3 "정직한 최선 추정치"):
- GET /api/v2/spot/trade/orderInfo         (orderId **또는** clientOid)
- GET /api/v2/spot/trade/unfilled-orders
- GET /api/v2/spot/trade/history-orders
- GET /api/v2/spot/trade/fills             (startTime ms — **미검증**)

심볼 변환("registry") — 스펙 §2-B는 `SymbolRegistry`로의 교체를 적지만
LA-19(`bitget/symbols.py`)가 이미 R8을 `symbol_normalizer` 단일 규칙으로
해소했고, 빈 `SymbolRegistry`는 fail-closed라 기존 어댑터 테스트를 무수정
통과시킬 수 없다(DoD 충돌). 여기서는 LA-19 위임을 유지한다 — 레지스트리
주입은 L4-16/24 호출부가 정규 심볼을 넘기는 시점에 결정한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.http_client import SignedRequestClient

# 확인된 값(라이브/문서 조사) + 미확인 값은 UNKNOWN으로 안전하게 폴백
# (8.3 원칙 — 모르는 상태를 실패로 단정하지 않는다).
# L4-13 — 스펙 §2-B 지시로 "new"/"init"(주문 접수 직후, 체결 전) →
# ACKNOWLEDGED 추가. **미검증**: Bitget V2 문서의 status 열거에 있으나
# 실제 스팟 응답에서 관측되지 않았다(Demo 키 확보 후 L4-30에서 확정).
_STATUS_MAP = {
    "init": OrderStatus.ACKNOWLEDGED,
    "new": OrderStatus.ACKNOWLEDGED,
    "live": OrderStatus.ACKNOWLEDGED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
}


def _map_status(raw_status: str) -> OrderStatus:
    return _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)


def _parse_bitget_timestamp(raw_ts: str | None) -> datetime:
    """Bitget는 밀리초 epoch를 문자열로 준다(cTime/uTime 등, 기존
    place-order 응답 조사와 동일 관례). 값이 없으면(구버전 응답 등)
    호출 시점으로 안전하게 폴백한다(8.3 원칙 — 모르는 값을 실패로
    단정하지 않는다)."""
    if not raw_ts:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(raw_ts) / 1000, tz=timezone.utc)


def _to_epoch_ms(since: datetime) -> str:
    """tz-aware만 허용(fail-closed) — naive datetime을 로컬/UTC 중 무엇으로
    해석하든 추측이고, 잘못 추측하면 체결 창이 시간대만큼 비어 대사가
    조용히 틀어진다."""
    if since.tzinfo is None or since.utcoffset() is None:
        raise ValueError("get_fills(since=)는 tz-aware datetime이어야 합니다(naive 거부).")
    return str(int(since.timestamp() * 1000))


def _row_to_order(data: dict[str, Any]) -> Order:
    """orderInfo/unfilled-orders/history-orders 3개 엔드포인트가 공유하는
    행 형태 — Bitget 스팟 거래 API는 이 3곳에서 동일한 필드 이름을 쓴다
    (place-order/orderInfo에서 이미 확인된 규칙과 동일).

    2026-09-03 거래소 내구성 감사 반영(FULL_AUDIT §2-B ③) — 이전에는
    priceAvg/price/cTime을 버려 average_fill_price가 항상 None으로
    영속화됐다. `priceAvg`(평균체결가) 우선, 없으면(미체결 주문 등)
    `price`(지정가)로 폴백 — 둘 다 없거나 "0"이면 시장가 미체결처럼
    아직 가격 정보가 없는 상태이므로 None 유지."""
    status = _map_status(data.get("status", ""))
    filled_quantity = (
        Decimal(data["fillSize"])
        if "fillSize" in data
        else Decimal(data["size"]) if status == OrderStatus.FILLED else Decimal("0")
    )
    avg_price_raw = data.get("priceAvg") or data.get("price")
    average_fill_price = (
        Money(amount=Decimal(avg_price_raw), currency=Currency.USDT)
        if avg_price_raw and Decimal(avg_price_raw) != 0
        else None
    )
    return Order(
        order_id=uuid4(),
        exchange_order_id=data["orderId"],
        client_order_id=data.get("clientOid", ""),
        strategy_id="",  # 자리표시자 — 호출부가 DB 조회로 채워야 함
        strategy_version="",
        symbol=data.get("symbol", ""),
        exchange="bitget",
        side=OrderSide(data["side"].upper()) if "side" in data else OrderSide.BUY,
        order_type=(
            OrderType(data["orderType"].upper()) if "orderType" in data else OrderType.LIMIT
        ),
        quantity=Decimal(data.get("size", "0")),
        status=status,
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        created_at=_parse_bitget_timestamp(data.get("cTime")),
        updated_at=_parse_bitget_timestamp(data.get("uTime")),
        asset_class=AssetClass.CRYPTO,
    )


def _first_row(data: Any) -> dict[str, Any] | None:
    """orderInfo는 관측상 `data`를 1원소 리스트로 준다(기존 get_order 파싱과
    동일). 빈 리스트/None은 "해당 id 없음"으로 본다 — **미검증**: 실제
    Bitget이 미존재 id에 빈 data를 주는지, 비성공 code를 주는지는 Demo
    키로 확인 전이다. 비성공 code는 `_request`가 예외로 올리므로 여기
    도달하지 않는다(None으로 위장되지 않는다)."""
    if isinstance(data, list):
        return data[0] if data else None
    return data or None


class BitgetTradingQueryMixin:
    async def get_order(self: SignedRequestClient, order_id: str) -> Order:
        """편차: 02번 인터페이스가 order_id 하나만으로 완전한 Order를
        반환하도록 요구하지만, Bitget 응답에는 AIOS 전용 컨텍스트(strategy_id/
        strategy_version/asset_class 등)가 없다 — 거래소는 그 개념 자체를
        모른다. 여기서는 거래소가 실제로 아는 필드(상태·체결정보·가격)만
        신뢰할 수 있게 채우고, AIOS 전용 필드는 자리표시자로 둔다 — 호출부
        (Reconciliation, FD-9.6)가 기존 DB 행과 병합해 완성해야 한다."""
        raw = await self._request(
            "GET", "/api/v2/spot/trade/orderInfo", params={"orderId": order_id}
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return _row_to_order(data)

    async def find_order_by_client_id(
        self: SignedRequestClient, client_order_id: str
    ) -> Order | None:
        """L4-13 — §6 F5-a(UNKNOWN 해소)/F14(DUPLICATE_CLIENT_ID 채택)용
        client id 역조회. orderInfo의 `clientOid` 파라미터(orderId와 택일,
        문서 조사 기준 — **미검증**). `None` = 거래소가 그 id를 모른다는
        사실(`_first_row` 참조); 통신/인증 오류는 그대로 전파된다."""
        raw = await self._request(
            "GET", "/api/v2/spot/trade/orderInfo", params={"clientOid": client_order_id}
        )
        row = _first_row(raw.get("data"))
        return None if row is None else _row_to_order(row)

    async def get_open_orders(
        self: SignedRequestClient, symbol: str | None = None
    ) -> list[Order]:
        """02b 스펙 §3.2 — FD-4.5(UNKNOWN 재조회)/FD-16.4(실행 모니터링)
        보강용. L4-13부터 `ExchangeAdapter` ABC 계약(기본 구현 override)."""
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(
            "GET", "/api/v2/spot/trade/unfilled-orders", params=params or None
        )
        return [_row_to_order(row) for row in raw["data"]]

    async def get_order_history(
        self: SignedRequestClient, symbol: str | None = None, *, limit: int = 100
    ) -> list[Order]:
        """FD-6.4(재시작 시 정합성 복구) 보강용."""
        params: dict[str, Any] = {"limit": str(limit)}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(
            "GET", "/api/v2/spot/trade/history-orders", params=params
        )
        return [_row_to_order(row) for row in raw["data"]]

    async def get_fills(
        self: SignedRequestClient,
        symbol: str | None = None,
        *,
        order_id: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """개별 체결 내역(하나의 주문이 여러 번 나눠 체결될 수 있음) —
        `fill_normalizer.normalize_fill`(L4-05) 입력용 원시 행. 별도 모델을
        만들지 않고 raw dict를 그대로 반환한다(02b §2 모델 재사용 원칙).

        L4-13 — `since`(tz-aware) → `startTime`(ms epoch). Bitget V2 fills의
        startTime/endTime 파라미터는 문서 조사 기준이며 **미검증**(L4-30
        확정). naive datetime은 요청 전에 ValueError로 거부한다."""
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        if order_id is not None:
            params["orderId"] = order_id
        if since is not None:
            params["startTime"] = _to_epoch_ms(since)
        raw = await self._request(
            "GET", "/api/v2/spot/trade/fills", params=params or None
        )
        return list(raw["data"])
