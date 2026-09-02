"""02d_kis_api_full_spec_v1.md §6 — WebSocket 메시지 파싱 단위테스트.

실제 소켓 없이도 검증 가능한 부분(순수 파싱 함수 + AES 왕복)만 다룬다 —
연결관리/재연결 루프는 tests/integration/test_kis_websocket.py 참조.
"""
import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from src.exchanges.kis.websocket_mixin import (
    _ORDER_NOTICE_FIELDS,
    _PRICE_FIELDS,
    decrypt_aes256_cbc,
    parse_order_notification_message,
    parse_realtime_orderbook_message,
    parse_realtime_price_message,
)


def _price_frame(**overrides: str) -> str:
    values = {name: "" for name in _PRICE_FIELDS}
    values.update(
        {
            "MKSC_SHRN_ISCD": "005930",
            "STCK_PRPR": "70000",
            "BIDP1": "69900",
            "ASKP1": "70100",
            "ACML_VOL": "12345",
        }
    )
    values.update(overrides)
    body = "^".join(values[name] for name in _PRICE_FIELDS)
    return f"0|H0STCNT0|001|{body}"


def test_parse_realtime_price_message_parses_frame():
    tickers = parse_realtime_price_message(_price_frame())

    assert len(tickers) == 1
    assert tickers[0].symbol == "005930"
    assert tickers[0].price.to_eng_string() == "70000"
    assert tickers[0].bid.to_eng_string() == "69900"


def test_parse_realtime_price_message_ignores_other_tr_id():
    frame = _price_frame().replace("H0STCNT0", "H0STASP0")
    assert parse_realtime_price_message(frame) == []


def test_parse_realtime_price_message_ignores_malformed_frame():
    assert parse_realtime_price_message("not-a-valid-frame") == []


def test_parse_realtime_orderbook_message_parses_frame():
    fields = [
        "005930", "091500", "0",
        *[f"{70100 + i * 10}" for i in range(10)],  # ASKP1..10
        *[f"{69900 - i * 10}" for i in range(10)],  # BIDP1..10
        *["10"] * 10,  # ASKP_RSQN1..10
        *["20"] * 10,  # BIDP_RSQN1..10
        "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
    ]
    body = "^".join(fields)
    frame = f"0|H0STASP0|001|{body}"

    book = parse_realtime_orderbook_message(frame)

    assert book is not None
    assert book.symbol == "005930"
    assert book.asks[0].price.to_eng_string() == "70100"
    assert book.bids[0].price.to_eng_string() == "69900"


def test_parse_realtime_orderbook_message_ignores_other_tr_id():
    assert parse_realtime_orderbook_message("0|H0STCNT0|001|x^y") is None


def test_decrypt_aes256_cbc_round_trips():
    """이 세션이 검증할 수 있는 것: AES 루틴 자체의 정확성(암호화한
    값을 다시 복호화하면 원문이 나온다). KIS 서버가 실제로 이 인코딩
    관례(원문 key/iv를 UTF-8로 사용)를 따르는지는 라이브 검증 필요
    (모듈 docstring 참조)."""
    key = "abcdefghijklmnop1234567890ABCDEF"[:32]
    iv = "1234567890abcdef"
    plaintext = "005930^091500^70000^..."

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8")))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    ciphertext_b64 = base64.b64encode(ciphertext).decode("ascii")

    result = decrypt_aes256_cbc(ciphertext_b64, key, iv)

    assert result == plaintext


def test_parse_order_notification_message_plaintext():
    values = {name: "" for name in _ORDER_NOTICE_FIELDS}
    values.update(
        {
            "ODER_NO": "999",
            "STCK_SHRN_ISCD": "005930",
            "SELN_BYOV_CLS": "02",
            "ODER_QTY": "10",
            "CNTG_QTY": "10",
            "CNTG_YN": "1",
        }
    )
    body = "^".join(values[name] for name in _ORDER_NOTICE_FIELDS)
    frame = f"0|H0STCNI0|001|{body}"

    order = parse_order_notification_message(frame, key="unused", iv="unused")

    assert order is not None
    assert order.exchange_order_id == "999"
    assert order.symbol == "005930"


def test_parse_order_notification_message_ignores_other_tr_id():
    assert parse_order_notification_message("0|H0STCNT0|001|x", key="k", iv="i") is None
