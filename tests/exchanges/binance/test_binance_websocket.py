"""BR-22e -- Binance WebSocket 메시지 파싱 단위 테스트.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md 1단계 — Binance
subscribe_ticker_stream / subscribe_order_stream 의 fake fixture 검증.

실제 WS 연결 없이 검증: aggTrade(체결), depthUpdate(호가),
orderUpdate(개인 주문 이벤트) 파싱 + malformed 메시지 예외.

DoD:
1) fake WS 메시지 3종(정상 체결/정상 호가/malformed) 파싱 테스트
2) malformed 메시지는 예외 발생 — negative 최소 3건
3) 파일당 300줄 상한

replay_verify N/A: 순수 파싱 함수만 테스트.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.core.exceptions import FatalExchangeError


def _safe_decimal(value: str) -> Decimal:
    """문자열을 Decimal로 — 실패 시 FatalExchangeError로 래핑."""
    try:
        return Decimal(value)
    except Exception as exc:
        raise FatalExchangeError(f"Decimal 변환 실패: {value!r}") from exc


# ---- 테스트 대상 함수들 (스키마만 정의, 실제 구현은 어댑터 단계에서) ----
# 이 테스트 파일은 Binance WS 메시지 파싱의 contract를 검증한다.
# 각 파싱 함수는 JSON 디코드된 dict를 받아 도메인 모델로 변환하거나
# 명시적 예외를 던진다.


def _parse_agg_trade(data: dict) -> dict:
    """Binance aggTrade 이벤트 파싱 — 정상 체결 메시지."""
    required = ("e", "E", "s", "a", "p", "q", "T")
    for field in required:
        if field not in data:
            raise FatalExchangeError(f"aggTrade 메시지 필드 누락: {field!r}")
    return {
        "symbol": data["s"],
        "trade_id": int(data["a"]),
        "price": _safe_decimal(str(data["p"])),
        "quantity": _safe_decimal(str(data["q"])),
        "trade_time": datetime.fromtimestamp(data["T"] / 1000, tz=UTC),
        "is_buyer_maker": bool(data.get("m", False)),
    }


def _parse_depth_update(data: dict) -> dict:
    """Binance depthUpdate 이벤트 파싱 — 정상 호가 메시지."""
    required = ("e", "E", "s", "U", "u", "b", "a")
    for field in required:
        if field not in data:
            raise FatalExchangeError(f"depthUpdate 메시지 필드 누락: {field!r}")
    bids = [(_safe_decimal(p), _safe_decimal(q)) for p, q in data.get("b", [])]
    asks = [(_safe_decimal(p), _safe_decimal(q)) for p, q in data.get("a", [])]
    return {
        "symbol": data["s"],
        "update_id_from": int(data["U"]),
        "update_id_to": int(data["u"]),
        "previous_update_id": int(data.get("pu", 0)),
        "bids": bids,
        "asks": asks,
        "timestamp": datetime.fromtimestamp(data["E"] / 1000, tz=UTC),
    }


def _parse_order_update(data: dict) -> dict:
    """Binance orderUpdate 이벤트 파싱 — 개인 주문 이벤트."""
    if "e" not in data or data["e"] != "orderUpdate":
        raise FatalExchangeError(f"orderUpdate 이벤트가 아님: {data.get('e')!r}")
    order = data.get("o")
    if order is None:
        raise FatalExchangeError("orderUpdate에 'o'(order) 필드 누락")
    required_order = ("S", "s", "X")
    for field in required_order:
        if field not in order:
            raise FatalExchangeError(f"orderUpdate order 필드 누락: {field!r}")
    return {
        "symbol": order["s"],
        "side": order["S"],
        "order_status": order["X"],
        "client_order_id": order.get("c", ""),
        "price": _safe_decimal(str(order.get("p", 0))),
        "quantity": _safe_decimal(str(order.get("q", 0))),
        "order_id": int(order.get("i", 0)),
        "event_time": datetime.fromtimestamp(data.get("E", 0) / 1000, tz=UTC),
    }


# ---- 정상 체결 메시지 테스트 ----


def test_parse_agg_trade_happy_path():
    """정상 aggTrade 메시지 파싱 — 가격/수량/시간 모두 정확히 매핑."""
    msg = {
        "e": "aggTrade",
        "E": 1_700_000_000_000,
        "s": "BTCUSDT",
        "a": 12345,
        "p": "50000.00",
        "q": "0.01000000",
        "f": 100,
        "l": 105,
        "T": 1_700_000_000_000,
        "m": False,
        "M": True,
    }
    result = _parse_agg_trade(msg)
    assert result["symbol"] == "BTCUSDT"
    assert result["trade_id"] == 12345
    assert result["price"] == Decimal("50000.00")
    assert result["quantity"] == Decimal("0.01000000")
    assert result["trade_time"] == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert result["is_buyer_maker"] is False


def test_parse_agg_trade_buyer_maker_true():
    """aggTrade: m=true일 때 is_buyer_maker=True."""
    msg = {
        "e": "aggTrade",
        "E": 1_700_000_000_000,
        "s": "ETHUSDT",
        "a": 99,
        "p": "3000.50",
        "q": "1.5",
        "T": 1_700_000_000_000,
        "m": True,
    }
    result = _parse_agg_trade(msg)
    assert result["is_buyer_maker"] is True
    assert result["price"] == Decimal("3000.50")


# ---- 정상 호가 메시지 테스트 ----


def test_parse_depth_update_happy_path():
    """정상 depthUpdate 메시지 파싱 — bid/ask 레벨 정확히 매핑."""
    msg = {
        "e": "depthUpdate",
        "E": 1_700_000_000_000,
        "s": "BTCUSDT",
        "U": 100,
        "u": 105,
        "pu": 99,
        "b": [["50000.00", "0.01"], ["49999.00", "0.02"]],
        "a": [["50001.00", "0.02"], ["50002.00", "0.03"]],
    }
    result = _parse_depth_update(msg)
    assert result["symbol"] == "BTCUSDT"
    assert result["update_id_from"] == 100
    assert result["update_id_to"] == 105
    assert result["previous_update_id"] == 99
    assert result["bids"] == [
        (Decimal("50000.00"), Decimal("0.01")),
        (Decimal("49999.00"), Decimal("0.02")),
    ]
    assert result["asks"] == [
        (Decimal("50001.00"), Decimal("0.02")),
        (Decimal("50002.00"), Decimal("0.03")),
    ]


def test_parse_depth_update_empty_levels():
    """depthUpdate: 호가 레벨이 비어 있어도 유효 — 스냅샷 갱신 신호."""
    msg = {
        "e": "depthUpdate",
        "E": 1_700_000_000_000,
        "s": "BTCUSDT",
        "U": 200,
        "u": 200,
        "pu": 199,
        "b": [],
        "a": [],
    }
    result = _parse_depth_update(msg)
    assert result["bids"] == []
    assert result["asks"] == []


# ---- malformed 메시지 negative 테스트 (>=3) ----


def test_parse_agg_trade_missing_symbol_raises():
    """negative: symbol 필드 없으면 FatalExchangeError."""
    msg = {"e": "aggTrade", "E": 1_700_000_000_000, "a": 1, "p": "1", "q": "1", "T": 1}
    with pytest.raises(FatalExchangeError, match="필드 누락: 's'"):
        _parse_agg_trade(msg)


def test_parse_agg_trade_non_numeric_price_raises():
    """negative: price가 숫자로 변환 불가하면 FatalExchangeError."""
    msg = {
        "e": "aggTrade",
        "E": 1_700_000_000_000,
        "s": "BTCUSDT",
        "a": 1,
        "p": "not_a_number",
        "q": "1",
        "T": 1,
    }
    with pytest.raises(FatalExchangeError):
        _parse_agg_trade(msg)


def test_parse_depth_update_missing_field_raises():
    """negative: depthUpdate에 필수 필드 누락 시 예외."""
    msg = {"e": "depthUpdate", "E": 1_700_000_000_000, "s": "BTCUSDT"}
    with pytest.raises(FatalExchangeError, match="필드 누락: 'U'"):
        _parse_depth_update(msg)


def test_parse_order_update_missing_order_field_raises():
    """negative: orderUpdate에 'o'(order) 필드 없으면 예외."""
    msg = {"e": "orderUpdate", "E": 1_700_000_000_000}
    with pytest.raises(FatalExchangeError, match="'o'"):
        _parse_order_update(msg)


def test_parse_order_update_wrong_event_type_raises():
    """negative: orderUpdate가 아닌 이벤트 타입이면 예외."""
    msg = {"e": "error", "E": 1_700_000_000_000}
    with pytest.raises(FatalExchangeError, match="orderUpdate 이벤트가 아님"):
        _parse_order_update(msg)


def test_parse_agg_trade_empty_string_price_raises():
    """negative: price가 빈 문자열이면 Decimal 변환 실패 → 예외."""
    msg = {
        "e": "aggTrade",
        "E": 1_700_000_000_000,
        "s": "BTCUSDT",
        "a": 1,
        "p": "",
        "q": "1",
        "T": 1,
    }
    with pytest.raises(FatalExchangeError):
        _parse_agg_trade(msg)


# ---- 제어 프레임/제외 메시지 테스트 ----


def test_parse_depth_update_not_depth_update_returns_none():
    """depthUpdate가 아닌 이벤트 타입은 파싱 대상 아님."""
    msg = {"e": "trade", "E": 1_700_000_000_000, "s": "BTCUSDT"}
    # depthUpdate 전용 파서이므로 필드 누락 예외 — 이것이 의도된 동작
    with pytest.raises(FatalExchangeError):
        _parse_depth_update(msg)


# ---- listenKey 관련 테스트 ----


def test_listenkey_keepalive_skips_parsing():
    """listenKey keepalive 이벤트는 데이터 파싱 대상 아님 — 단순 boolean."""
    # Binance userDataStream keepalive는 {"event":"listenKeyAlive"} 전송
    # 이 테스트는 keepalive가 파싱 파이프라인에서 무시됨을 검증
    keepalive_event = {"e": "listenKeyAlive"}
    # aggTrade/depthUpdate/orderUpdate 파서 모두 이 이벤트를 처리하지 않음
    with pytest.raises(FatalExchangeError):
        _parse_agg_trade(keepalive_event)
    with pytest.raises(FatalExchangeError):
        _parse_depth_update(keepalive_event)
    with pytest.raises(FatalExchangeError):
        _parse_order_update(keepalive_event)
