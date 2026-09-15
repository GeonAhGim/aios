"""NHAdapter WebSocket 데이터 프레임 파싱 -- 순수 함수(I/O 없음).

Spec: 02e_nh_api_spec_v1.md#§4, docs/exchanges/NH_GAPS.md#2.

2026-09-16(task-2615) 재조사 -- 이전 세션(task-114, websocket_mixin.py
모듈 docstring)은 공식 SDK 소스(`nhplug/realtime.py`)만 확인했고, 그
소스가 데이터 프레임의 `body` 내부 필드 파싱을 호출부에 위임하기
때문에 "필드 스키마 미확인"으로 남겼다. 이번에 자산군별 공식
OpenAPI 스펙(`https://www.nhplug.com/openapi-docs/krstock/openapi.json`,
도메인이 정본) 원문을 `curl`로 직접 내려받아 `json.load()`로 루트
키를 나열해보니 `x-realtime-channels` 키가 실제로 존재했다 -- 이전
WebFetch 조사는 문서가 커서(약 450KB) 요약 모델이 이 절에 도달하기
전에 잘랐을 뿐이다(자세한 경위는 NH_GAPS.md 참조).

`x-realtime-channels.channels[]`에서 tr_cd="mc"(국내주식
실시간체결가통합, KRX+NXT)의 확인된 `body` 필드 전체(29개, 공식
스펙 그대로): code, time, sign, change, price, chrate, high, low,
offer, bid, volume, volrate, movolume, value, open, avgprice,
janggubun, bidrate, volpower, new_volume, bidvolall, offvolall,
kospigb, value_won, marketgb, main_close, market_sign, market_change,
market_chrate. `Ticker` 매핑에 쓰는 5개(code/price/offer/bid/volume)는
전부 이 목록 안에 있다 -- 그 외 필드는 `Ticker` 모델에 대응 슬롯이
없어 사용하지 않는다(추측 아님, 스키마 확인 후 선택).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Ticker

_MC_TR_CD = "mc"


def parse_mc_ticker_frame(raw: str, *, exchange: str = "nh") -> Ticker | None:
    """`tr_cd="mc"` 데이터 프레임만 `Ticker`로 변환한다.

    구독 ack 프레임(header에 `tr_type`/`rsp_cd` 존재 -- websocket_mixin.py
    모듈 docstring의 구분 규칙)과 다른 채널(`mb`/`d2` 등)의 데이터
    프레임은 이 파서의 책임이 아니므로 `None`을 반환해 조용히
    무시한다. 반면 `mc` 데이터 프레임인데 확인된 필드가 없으면(서버가
    스펙과 다르게 응답한 경우) 추측으로 채우지 않고 즉시
    `FatalExchangeError`로 실패한다."""
    try:
        frame: Any = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise FatalExchangeError(
            f"NH WS 프레임이 JSON이 아님(x-realtime-channels 확인 기준 항상 JSON): {raw!r}"
        ) from exc
    if not isinstance(frame, dict):
        raise FatalExchangeError(f"NH WS 프레임이 JSON 객체가 아님: {raw!r}")
    header = frame.get("header")
    body = frame.get("body")
    if not isinstance(header, dict) or not isinstance(body, dict):
        raise FatalExchangeError(f"NH WS 프레임이 {{header, body}} 구조가 아님: {raw!r}")
    if "tr_type" in header or "rsp_cd" in header:
        return None  # 구독 ack -- 데이터 프레임이 아니므로 파싱 대상 아님
    if header.get("tr_cd") != _MC_TR_CD:
        return None  # 다른 채널의 데이터 -- 이 파서 범위 밖
    try:
        return Ticker(
            symbol=str(body["code"]),
            exchange=exchange,
            price=Decimal(str(body["price"])),
            bid=Decimal(str(body["bid"])),
            ask=Decimal(str(body["offer"])),
            volume_24h=Decimal(str(body["volume"])),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )
    except KeyError as exc:
        raise FatalExchangeError(
            f"NH mc 채널 프레임에 예상 필드 없음(공식 x-realtime-channels 기준 "
            f"code/price/offer/bid/volume 필요, NH_GAPS.md §2 참조): {exc}"
        ) from exc
