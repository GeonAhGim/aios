"""02d_kis_api_full_spec_v1.md §6 — KISAdapter WebSocket(실시간) 메서드군.

Spec: 02d_kis_api_full_spec_v1.md §6, §7(작업 분해 6번)

기존 `subscribe_ticker_stream()`은 `NotImplementedError`로 막혀 있었다
(승인키 인증 체계 미확인, 6.9/6.10 스콥 밖으로 문서화됨) — 이번 조사
(WebFetch, github.com/koreainvestment/open-trading-api/examples_user/
{kis_auth.py, domestic_stock/domestic_stock_functions_ws.py},
2026-09-02)로 실제 예제 소스코드를 직접 확인해 구현한다.

**메시지 형식(공식 예제 파싱 코드로 직접 확인, 높은 신뢰도)**:
- 제어 메시지(구독 ack, PINGPONG)는 JSON — `{"header": {...}, "body": {...}}`
- 데이터 메시지는 파이프(`|`) 4단 분할: `암호화플래그|tr_id|데이터건수|본문`,
  본문 내부는 캐럿(`^`)으로 필드 구분 — `raw.split("|")`, `body.split("^")`가
  공식 예제 파싱 로직 그대로.
- PINGPONG: 헤더의 tr_id가 "PINGPONG"이면 받은 메시지를 그대로 pong
  프레임으로 돌려보내야 한다(`ws.pong(raw)`, 공식 예제 확인) — 안 하면
  서버가 연결을 끊는다.
- 체결통보(H0STCNI0/데모 H0STCNI9)는 **암호화**된다 — 구독 ack의
  `body.output.key`/`body.output.iv`(AES-256-CBC)로 이후 데이터를
  복호화해야 한다(공식 예제 docstring + 파싱 코드로 확인). 이 세션은
  AES 루틴 자체는 왕복 테스트로 검증했지만, KIS 서버가 실제로 내려주는
  key/iv 인코딩(원문 그대로인지 base64인지 등)은 라이브 응답으로만
  확정 가능 — 최선 추정치(공식 예제 관례: 원문 문자열 그대로 UTF-8
  인코딩해 키로 사용).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from websockets.asyncio.client import connect as _default_connect
from websockets.exceptions import ConnectionClosed

from src.data.models.base import AssetClass
from src.data.models.market_data import OrderBook, OrderBookLevel, Ticker
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.types import TickerCallback

logger = logging.getLogger(__name__)

WS_REAL_URL = "ws://ops.koreainvestment.com:21000"
WS_PAPER_URL = "ws://ops.koreainvestment.com:31000"

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

ReconnectHook = Callable[[], Awaitable[None]]
OrderBookCallback = Callable[[OrderBook], Awaitable[None]]
OrderCallback = Callable[[Order], Awaitable[None]]
MessageHandler = Callable[[str], Awaitable[None]]


class WsConnection(Protocol):
    async def send(self, message: str) -> None: ...
    async def pong(self, data: bytes | str = b"") -> None: ...
    def __aiter__(self) -> AsyncIterator[str]: ...


ConnectFn = Callable[[str], AbstractAsyncContextManager[WsConnection]]


def _connect(url: str) -> AbstractAsyncContextManager[WsConnection]:
    return _default_connect(url)  # type: ignore[return-value]


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


async def _run_kis_ws_subscription(
    url: str,
    subscribe_msg: dict[str, Any],
    on_data_frame: MessageHandler,
    *,
    on_key_iv: Callable[[str, str], None] | None = None,
    connect_fn: ConnectFn = _connect,
    on_reconnecting: ReconnectHook | None = None,
    on_reconnected: ReconnectHook | None = None,
    max_backoff_seconds: float = 30.0,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Bitget의 `_run_ws_subscription()`(market_data_mixin.py)과 동일한
    연결관리/재연결/백오프 책임을 지지만, KIS는 메시지 형식 자체가
    근본적으로 달라(JSON 제어 vs 파이프 데이터, PINGPONG 필수 응답)
    같은 함수를 재사용할 수 없다 — 별도 구현(§2.1 재연결 책임 원칙은
    로직 형태가 아니라 "책임"의 재사용, 코드 재사용까지 강제하지 않음)."""
    backoff = 1.0
    first_attempt = True

    while True:
        if not first_attempt and on_reconnecting is not None:
            await on_reconnecting()
        first_attempt = False
        try:
            async with connect_fn(url) as ws:
                await ws.send(json.dumps(subscribe_msg))
                if backoff > 1.0 and on_reconnected is not None:
                    await on_reconnected()
                backoff = 1.0
                async for raw in ws:
                    if _is_json_message(raw):
                        message = json.loads(raw)
                        header = message.get("header", {})
                        if header.get("tr_id") == "PINGPONG":
                            await ws.pong(raw)
                            continue
                        body = message.get("body", {})
                        output = body.get("output")
                        if on_key_iv is not None and isinstance(output, dict) and "key" in output:
                            on_key_iv(output["key"], output["iv"])
                        continue
                    await on_data_frame(raw)
        except (ConnectionClosed, OSError) as exc:
            logger.warning("KIS WS 연결 끊김: %s — %.1f초 후 재연결", exc, backoff)
            await sleep_fn(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)


class KISWebSocketMixin:
    async def get_ws_approval_key(self) -> str:
        """REST 접근토큰(`_ensure_token()`)과 별개의 WS 전용 승인키 —
        `secretkey` 필드명이 REST의 `appsecret`과 다름(공식 예제 확인)."""
        response = await self._client.post(  # type: ignore[attr-defined]
            "/oauth2/Approval",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,  # type: ignore[attr-defined]
                "secretkey": self._app_secret,  # type: ignore[attr-defined]
            },
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        return str(response.json()["approval_key"])

    async def subscribe_ticker_stream(
        self,
        symbol: str,
        callback: TickerCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02d 스펙 §6(P0) — 기존 NotImplementedError를 실제 구현으로
        대체(승인키 인증 확인 완료, 모듈 docstring 참조)."""
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL  # type: ignore[attr-defined]
        subscribe_msg = _build_subscribe_message(approval_key, "H0STCNT0", symbol)

        async def on_data_frame(raw: str) -> None:
            for ticker in parse_realtime_price_message(raw):
                await callback(ticker)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            on_data_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_orderbook_stream(
        self,
        symbol: str,
        callback: OrderBookCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """`ExchangeAdapter` ABC에는 아직 없음(Bitget 확장 메서드들과
        동일 원칙 — 소비하는 FD-2 호출부가 생기기 전까지 KIS 전용)."""
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL  # type: ignore[attr-defined]
        subscribe_msg = _build_subscribe_message(approval_key, "H0STASP0", symbol)

        async def on_data_frame(raw: str) -> None:
            book = parse_realtime_orderbook_message(raw)
            if book is not None:
                await callback(book)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            on_data_frame,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_order_notification_stream(
        self,
        callback: OrderCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02d 스펙 §6(P1) — FD-4.5류 재조회를 실시간으로 대체할 후보
        (Bitget WS orders 채널과 동일 가치). **암호화 채널**이라 다른
        두 스트림보다 신뢰도가 낮다 — 모듈 docstring의 AES 관련 caveat
        참조. `tr_key`는 문서 관례상 공백(계좌 전체 대상)."""
        approval_key = await self.get_ws_approval_key()
        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL  # type: ignore[attr-defined]
        tr_id = "H0STCNI9" if self._is_paper_trading else "H0STCNI0"  # type: ignore[attr-defined]
        subscribe_msg = _build_subscribe_message(approval_key, tr_id, "")

        key_iv: dict[str, str] = {}

        def on_key_iv(key: str, iv: str) -> None:
            key_iv["key"] = key
            key_iv["iv"] = iv

        async def on_data_frame(raw: str) -> None:
            if "key" not in key_iv:
                return
            order = parse_order_notification_message(raw, key=key_iv["key"], iv=key_iv["iv"])
            if order is not None:
                await callback(order)

        await _run_kis_ws_subscription(
            url,
            subscribe_msg,
            on_data_frame,
            on_key_iv=on_key_iv,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )
