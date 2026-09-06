"""02d_kis_api_full_spec_v1.md §6 — KIS WS 메시지 파싱(파이프/캐럿 분할, AES 복호화).

Spec: 02d_kis_api_full_spec_v1.md §6, §7(작업 분해 6번)

task-1723 P1-D: websocket_mixin.py(422줄, P6 300줄 초과)의 순수 파싱 로직을
분리. 공개 심볼은 websocket_mixin.py가 그대로 재수출(테스트가 그 경로로
직접 import — tests/unit/exchanges/test_kis_ws_messages.py,
tests/integration/test_kis_websocket.py).
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from src.data.models.base import AssetClass
from src.data.models.market_data import OrderBook, OrderBookLevel, Ticker
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType

# 실시간 체결가(H0STCNT0) 필드 순서 — 공식 예제(domestic_stock_functions_ws.py
# ::ccnl_krx()) 그대로.
_PRICE_FIELDS = [
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN", "PRDY_VRSS",
    "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR",
    "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL", "ACML_TR_PBMN", "SELN_CNTG_CSNU",
    "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "CTTR", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN",
    "CCLD_DVSN", "SHNU_RATE", "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_PRPR", "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR",
    "BSOP_DATE", "NEW_MKOP_CLS_CODE", "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "VOL_TNRT", "PRDY_SMNS_HOUR_ACML_VOL",
    "PRDY_SMNS_HOUR_ACML_VOL_RATE", "HOUR_CLS_CODE", "MRKT_TRTM_CLS_CODE",
    "VI_STND_PRC",
]  # fmt: skip


def _build_orderbook_fields() -> list[str]:
    fields = ["MKSC_SHRN_ISCD", "BSOP_HOUR", "HOUR_CLS_CODE"]
    fields += [f"ASKP{i}" for i in range(1, 11)]
    fields += [f"BIDP{i}" for i in range(1, 11)]
    fields += [f"ASKP_RSQN{i}" for i in range(1, 11)]
    fields += [f"BIDP_RSQN{i}" for i in range(1, 11)]
    fields += [
        "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "OVTM_TOTAL_ASKP_RSQN",
        "OVTM_TOTAL_BIDP_RSQN", "ANTC_CNPR", "ANTC_CNQN", "ANTC_VOL",
        "ANTC_CNTG_VRSS", "ANTC_CNTG_VRSS_SIGN", "ANTC_CNTG_PRDY_CTRT",
        "ACML_VOL", "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC",
        "OVTM_TOTAL_ASKP_ICDC", "OVTM_TOTAL_BIDP_ICDC", "STCK_DEAL_CLS_CODE",
    ]  # fmt: skip
    return fields


_ORDERBOOK_FIELDS = _build_orderbook_fields()

# 체결통보(H0STCNI0/H0STCNI9) 필드 순서 — 공식 예제(ccnl_notice()) 그대로.
_ORDER_NOTICE_FIELDS = [
    "CUST_ID", "ACNT_NO", "ODER_NO", "OODER_NO", "SELN_BYOV_CLS", "RCTF_CLS",
    "ODER_KIND", "ODER_COND", "STCK_SHRN_ISCD", "CNTG_QTY", "CNTG_UNPR",
    "STCK_CNTG_HOUR", "RFUS_YN", "CNTG_YN", "ACPT_YN", "BRNC_NO", "ODER_QTY",
    "ACNT_NAME", "ORD_COND_PRC", "ORD_EXG_GB", "POPUP_YN", "FILLER",
    "CRDT_CLS", "CRDT_LOAN_DATE", "CNTG_ISNM40", "ODER_PRC",
]  # fmt: skip


def _split_ws_frame(raw: str) -> tuple[str, str, str, str] | None:
    """데이터 프레임(파이프 4단 분할) 파싱. JSON 제어 메시지는 이 함수가
    다루지 않는다(호출부가 `raw.startswith("{")`로 먼저 구분)."""
    parts = raw.split("|", 3)
    if len(parts) < 4:
        return None
    return parts[0], parts[1], parts[2], parts[3]


def _split_records(body: str, field_names: list[str]) -> list[dict[str, str]]:
    """본문(^ 구분)을 레코드 스키마 길이만큼 잘라 여러 레코드로 분리한다
    (data_count>1일 때 레코드가 이어붙어 오는 공식 관례)."""
    values = body.split("^")
    n = len(field_names)
    records = []
    for i in range(0, len(values), n):
        chunk = values[i : i + n]
        if len(chunk) < n:
            break
        records.append(dict(zip(field_names, chunk, strict=True)))
    return records


def parse_realtime_price_message(raw: str) -> list[Ticker]:
    frame = _split_ws_frame(raw)
    if frame is None:
        return []
    _encrypt_flag, tr_id, _count, body = frame
    if tr_id != "H0STCNT0":
        return []
    tickers = []
    for row in _split_records(body, _PRICE_FIELDS):
        tickers.append(
            Ticker(
                symbol=row["MKSC_SHRN_ISCD"],
                exchange="kis",
                price=Decimal(row["STCK_PRPR"]),
                bid=Decimal(row["BIDP1"]),
                ask=Decimal(row["ASKP1"]),
                volume_24h=Decimal(row["ACML_VOL"]),
                timestamp=datetime.now(timezone.utc),
                source_type="primary",
            )
        )
    return tickers


def parse_realtime_orderbook_message(raw: str) -> OrderBook | None:
    frame = _split_ws_frame(raw)
    if frame is None:
        return None
    _encrypt_flag, tr_id, _count, body = frame
    if tr_id != "H0STASP0":
        return None
    records = _split_records(body, _ORDERBOOK_FIELDS)
    if not records:
        return None
    row = records[0]
    bids = [
        OrderBookLevel(price=Decimal(row[f"BIDP{i}"]), quantity=Decimal(row[f"BIDP_RSQN{i}"]))
        for i in range(1, 11)
        if row.get(f"BIDP{i}")
    ]
    asks = [
        OrderBookLevel(price=Decimal(row[f"ASKP{i}"]), quantity=Decimal(row[f"ASKP_RSQN{i}"]))
        for i in range(1, 11)
        if row.get(f"ASKP{i}")
    ]
    return OrderBook(
        symbol=row["MKSC_SHRN_ISCD"],
        exchange="kis",
        bids=bids,
        asks=asks,
        timestamp=datetime.now(timezone.utc),
    )


def decrypt_aes256_cbc(ciphertext_b64: str, key: str, iv: str) -> str:
    """체결통보 채널 복호화 — AES-256-CBC, PKCS7 패딩(공식 예제 docstring
    확인). `key`/`iv`는 구독 ack의 `body.output.{key,iv}`를 UTF-8로 그대로
    인코딩해 사용한다는 것이 커뮤니티 구현 관례(라이브 검증 필요 — KIS가
    실제로 원문 그대로 주는지 별도 인코딩을 쓰는지는 이 세션이 확정할
    수 없음). 이 함수 자체(AES 루틴)는 왕복 테스트로 검증됨."""
    key_bytes = key.encode("utf-8")
    iv_bytes = iv.encode("utf-8")
    ciphertext = base64.b64decode(ciphertext_b64)
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


def parse_order_notification_message(raw: str, *, key: str, iv: str) -> Order | None:
    frame = _split_ws_frame(raw)
    if frame is None:
        return None
    encrypt_flag, tr_id, _count, body = frame
    if tr_id not in ("H0STCNI0", "H0STCNI9"):
        return None
    plaintext = decrypt_aes256_cbc(body, key, iv) if encrypt_flag == "1" else body
    records = _split_records(plaintext, _ORDER_NOTICE_FIELDS)
    if not records:
        return None
    row = records[0]
    return Order(
        order_id=uuid4(),
        exchange_order_id=row.get("ODER_NO", ""),
        client_order_id="",
        strategy_id="",
        strategy_version="",
        symbol=row.get("STCK_SHRN_ISCD", ""),
        exchange="kis",
        side=OrderSide.SELL if row.get("SELN_BYOV_CLS") == "01" else OrderSide.BUY,
        # ODER_KIND(주문종류코드)가 시장가/지정가를 구분하지만 코드값
        # 매핑을 이번 조사에서 확인하지 못했다 — 다른 거래소 어댑터와
        # 동일하게 안전한 기본값(LIMIT)으로 폴백한다(8.3 원칙).
        order_type=OrderType.LIMIT,
        quantity=Decimal(row.get("ODER_QTY", "0") or "0"),
        status=OrderStatus.FILLED if row.get("CNTG_YN") == "1" else OrderStatus.ACKNOWLEDGED,
        filled_quantity=Decimal(row.get("CNTG_QTY", "0") or "0"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        asset_class=AssetClass.KR_EQUITY,
    )


def _is_json_message(raw: str) -> bool:
    return raw.lstrip().startswith("{")


def _build_subscribe_message(approval_key: str, tr_id: str, tr_key: str) -> dict[str, Any]:
    """공식 예제(kis_auth.py::data_fetch()) 그대로 재구성한 구독 메시지
    JSON 봉투."""
    return {
        "header": {
            "approval_key": approval_key,
            "custtype": "P",
            "tr_type": "1",
            "content-type": "utf-8",
        },
        "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
    }
