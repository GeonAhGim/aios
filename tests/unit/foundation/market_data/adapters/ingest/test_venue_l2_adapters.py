"""RD-19 — Binance/Bybit/OKX/Upbit `VenueL2Adapter` 파싱 단위테스트.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. REST 호출 없이(HTTP client 미사용) WS 메시지 파싱·시퀀스 추출·
ack 판정만 검증한다. 각 어댑터 docstring의 "미검증" 각주가 명시하듯
필드 스키마는 공개 문서 기억 기반이라 실거래소 라이브 응답과 다를 수
있다 — 이 테스트는 "우리 파서가 그 가정된 스키마를 정확히 반영하는가"만
증명한다.
"""
from __future__ import annotations

from decimal import Decimal

from src.foundation.market_data.adapters.ingest.binance_l2 import BinanceL2Adapter
from src.foundation.market_data.adapters.ingest.bybit_l2 import BybitL2Adapter
from src.foundation.market_data.adapters.ingest.okx_l2 import OkxL2Adapter
from src.foundation.market_data.adapters.ingest.upbit_l2 import UpbitL2Adapter
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot

# ---------- Binance ----------


def test_binance_parses_depth_update_diff():
    adapter = BinanceL2Adapter()
    message = {
        "e": "depthUpdate", "E": 1700000000000, "s": "BTCUSDT",
        "U": 157, "u": 160,
        "b": [["10.0", "1"], ["9.5", "0"]],
        "a": [["10.5", "2"]],
    }
    parsed = adapter.parse_event(message)
    assert isinstance(parsed, L2Diff)
    assert parsed.sequence == 160
    assert parsed.bid_updates == ((Decimal("10.0"), Decimal("1")), (Decimal("9.5"), Decimal("0")))
    assert adapter.seq_extractor(message) == 160


def test_binance_ignores_non_depth_frames():
    adapter = BinanceL2Adapter()
    assert adapter.parse_event({"e": "trade"}) is None
    assert adapter.seq_extractor({"e": "trade"}) is None


def test_binance_subscribe_ack_success_and_failure():
    adapter = BinanceL2Adapter()
    ok = adapter.ack_validator({"result": None, "id": 1})
    err = adapter.ack_validator({"error": {"code": -1, "msg": "bad"}, "id": 1})
    assert ok.is_ack and ok.ok
    assert err.is_ack and not err.ok


# ---------- Bybit ----------


def test_bybit_parses_orderbook_delta():
    adapter = BybitL2Adapter()
    message = {
        "topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": 1700000000000,
        "data": {"s": "BTCUSDT", "b": [["10.0", "1"]], "a": [["10.5", "0"]], "u": 42, "seq": 99},
    }
    parsed = adapter.parse_event(message)
    assert isinstance(parsed, L2Diff)
    assert parsed.sequence == 42
    assert adapter.seq_extractor(message) == 42


def test_bybit_subscribe_ack():
    adapter = BybitL2Adapter()
    ack = adapter.ack_validator({"op": "subscribe", "success": True})
    fail = adapter.ack_validator({"op": "subscribe", "success": False, "ret_msg": "denied"})
    assert ack.is_ack and ack.ok
    assert fail.is_ack and not fail.ok


# ---------- OKX ----------


def test_okx_parses_books_update():
    adapter = OkxL2Adapter()
    message = {
        "arg": {"channel": "books", "instId": "BTC-USDT"},
        "action": "update",
        "data": [
            {
                "bids": [["10.0", "1", "0", "1"]],
                "asks": [["10.5", "2", "0", "1"]],
                "ts": "1700000000000",
                "seqId": 555,
                "prevSeqId": 554,
            }
        ],
    }
    parsed = adapter.parse_event(message)
    assert isinstance(parsed, L2Diff)
    assert parsed.sequence == 555
    assert parsed.bid_updates == ((Decimal("10.0"), Decimal("1")),)
    assert adapter.seq_extractor(message) == 555


def test_okx_subscribe_ack_and_error():
    adapter = OkxL2Adapter()
    ok = adapter.ack_validator({"event": "subscribe", "arg": {"channel": "books"}})
    err = adapter.ack_validator({"event": "error", "msg": "bad instId"})
    assert ok.is_ack and ok.ok
    assert err.is_ack and not err.ok


# ---------- Upbit ----------


def test_upbit_parses_full_snapshot_and_has_no_sequence():
    adapter = UpbitL2Adapter()
    message = {
        "type": "orderbook", "code": "KRW-BTC", "timestamp": 1700000000000,
        "orderbook_units": [
            {"ask_price": 10.5, "bid_price": 10.0, "ask_size": 1.0, "bid_size": 2.0},
        ],
    }
    parsed = adapter.parse_event(message)
    assert isinstance(parsed, L2Snapshot)
    assert parsed.bids == {Decimal("10.0"): Decimal("2.0")}
    assert parsed.asks == {Decimal("10.5"): Decimal("1.0")}
    # Upbit는 매 프레임이 전체 스냅샷이라 시퀀스 갭 개념이 없다.
    assert adapter.seq_extractor(message) is None
    assert adapter.ack_validator(message).is_ack is False
