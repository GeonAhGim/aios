"""NHAdapter Trading 메서드군 + health_check().

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02e_nh_api_spec_v1.md#§3

2026-09-03(task-114) 재조사 — 공식 REST 클라이언트 소스(`nhplug/client.py`)
로 rsp_cd/rsp_msg 판정 로직을 재확인했고, 무엇보다 도메인이 정본(SSOT)이라고
명시된 자산군별 OpenAPI 스펙(`https://www.nhplug.com/openapi-docs/krstock/
openapi.json`, `nhplug-sdk` 레포 `docs/README.md`/`AGENTS.md`가 이 URL을
정본으로 지목)을 직접 내려받아 확인했다. 그 결과 이전 "추정" 3개
(정정/취소/주문조회)의 실제 경로와 place_order 응답 필드명이 **모두
틀렸다**는 것을 확인했다 — 이번 리프에서 아래처럼 수정한다.

**확인**(openapi.json 직접 확인, 이하 전부 동일 출처):
- 매수/매도: `POST /krstock/order/v1/cashBuy`|`cashSell` — 요청 필드는
  기존 추정과 대부분 일치했으나, **응답 주문번호 필드는 `odno`가 아니라
  `mkt_orr_no`(시장주문번호, int64)였다** — 이전 구현은 항상
  FatalExchangeError로 실패했을 것이다(존재하지 않는 필드를 찾았으므로).
- 정정: `POST /krstock/order/v1/modify`(이전 추정 `cashModify`는 존재하지
  않는 경로) — Input_0 필수 필드 `act_no, org_mkt_orr_no, all_pat_dit_cd,
  iem_cd, cor_qty, cor_pr, sop_cnd_pr, rmt_mkt_cd, sor_mkt_sli_yn`.
- 취소: `POST /krstock/order/v1/cancel`(이전 추정 `cashCancel`은 존재하지
  않는 경로) — Input_0 필수 필드 `act_no, org_mkt_orr_no, all_pat_dit_cd,
  iem_cd`.
- 두 엔드포인트 모두 대상 주문을 `org_mkt_orr_no`(place_order 응답의
  `mkt_orr_no`)로 지정하고, **`iem_cd`(종목코드)도 반드시 함께 보내야
  한다** — `ExchangeAdapter.cancel_order(order_id)`/`modify_order(order_id)`
  계약(common/adapter.py)엔 심볼 인자가 없으므로, KIS(`orgno:odno`)와
  동일한 이유로 `exchange_order_id`를 `"{iem_cd}:{mkt_orr_no}"` 합성키로
  구성한다(place_order 참조).

**확인된 구조적 불일치 — `get_order()`는 구현하지 않는다(§0-1 재조사
결론 격상)**: "주문조회"에 가장 가까운 카테고리는
`POST /krstock/inquiry/v1/dailyOrderExecution`(주식일별주문체결조회)였다.
그런데 이 응답(Output_1 배열)의 각 행은 `itg_orr_no`(통합주문번호)만
가지고 있고 **`mkt_orr_no` 필드가 없다** — place_order/modify_order가
반환하는 식별자(`mkt_orr_no`)와 dailyOrderExecution이 조회 가능한
식별자(`itg_orr_no`)가 서로 다른 번호 체계이고, 두 값을 연결할 필드가
응답 스키마 어디에도 없다. 즉 "추정 엔드포인트라 신뢰도 낮음"이 아니라
"공식 스펙으로 확인한 결과 이 경로로는 우리 exchange_order_id를 조회할
방법이 없다"는 것이 확정됐다 — 근거 없는 매핑(예: itg_orr_no==mkt_orr_no
가정)으로 조용히 틀린 Order를 만드는 대신 명시적으로 NotImplementedError.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.http_client import NHHTTPClient
from src.exchanges.common.live_guard import require_paper_sandbox

_MARKET_CODE = "KRX"


class _BalanceReadingClient(NHHTTPClient, Protocol):
    """health_check()가 같은 클래스의 get_balance()(account_mixin.py)를
    교차 호출하지만, self가 NHHTTPClient로 좁혀진 메서드 안에서는 그
    사실이 보이지 않으므로 명시적으로 계약에 포함한다(bitget
    `_TickerReadingClient`/kis `_IntradayCandleClient`와 동일 패턴)."""

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]: ...


def _order_division(order_type: OrderType) -> str:
    return "05" if order_type == OrderType.MARKET else "01"


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    """place_order()가 심는 합성키(`iem_cd:mkt_orr_no`) 분해 — 모듈
    docstring 참조(KIS `orgno:odno`와 동일한 이유)."""
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"NH exchange_order_id는 'iem_cd:mkt_orr_no' 형식이어야 함: {exchange_order_id}"
        )
    symbol, mkt_orr_no = exchange_order_id.split(":", 1)
    return symbol, mkt_orr_no


def _parse_mkt_orr_no(mkt_orr_no: str) -> int:
    """openapi.json 확인 — `org_mkt_orr_no`는 int64. 숫자가 아니면(합성키가
    손상됐거나 다른 거래소의 id가 잘못 전달된 경우) 조용히 잘못된 주문을
    건드리지 않고 즉시 실패한다."""
    try:
        return int(mkt_orr_no)
    except ValueError as exc:
        raise FatalExchangeError(f"NH mkt_orr_no가 정수가 아님: {mkt_orr_no!r}") from exc


class NHTradingMixin:
    @require_paper_sandbox
    async def place_order(self: NHHTTPClient, order: Order) -> Order:
        path = "/krstock/order/v1/cashBuy" if order.side == OrderSide.BUY else (
            "/krstock/order/v1/cashSell"
        )
        body: dict[str, Any] = {
            "act_no": self._act_no,
            "iem_cd": order.symbol,
            "orr_qty": str(order.quantity),
            "orr_pr": str(order.price.amount) if order.price is not None else "0",
            "nmn_pr_tp_cd": _order_division(order.order_type),
            "orr_cnd_dit_cd": "0",
            "ssl_nmn_pr_dit_cd": "0",
            "rmt_mkt_cd": _MARKET_CODE,
            "sor_mkt_sli_yn": "N",
        }
        raw = await self._request("POST", path, body=body)
        try:
            output = raw["Output_0"]
            mkt_orr_no = str(output["mkt_orr_no"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH 주문 응답에 예상 필드 없음(공식 openapi.json 기준 mkt_orr_no "
                f"필요, 02e 스펙 §3 참조): {exc}"
            ) from exc
        exchange_order_id = f"{order.symbol}:{mkt_orr_no}"
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    @require_paper_sandbox
    async def cancel_order(self: NHHTTPClient, order_id: str) -> bool:
        """`POST /krstock/order/v1/cancel`(공식 openapi.json 확인, 모듈
        docstring 참조). `_request()`가 이미 rsp_cd 실패를 예외로 올리므로
        (RetryableExchangeError/FatalExchangeError), 이 지점에 도달했다는
        것 자체가 성공을 뜻한다 — 이전 구현은 `raw.get("rsp_cd") == "00000"`
        로 재검사했는데, 성공 코드가 "00166"/"00221"/"13578"인 정상 취소를
        실패(False)로 잘못 보고하는 버그였다(실패경로 테스트로 발견)."""
        symbol, mkt_orr_no = _split_exchange_order_id(order_id)
        body = {
            "act_no": self._act_no,
            "org_mkt_orr_no": _parse_mkt_orr_no(mkt_orr_no),
            "all_pat_dit_cd": "1",  # 1=전체(잔량) 취소만 지원 — 부분 취소는 미구현
            "iem_cd": symbol,
        }
        await self._request("POST", "/krstock/order/v1/cancel", body=body)
        return True

    @require_paper_sandbox
    async def modify_order(self: NHHTTPClient, order_id: str, **kwargs: Any) -> Order:
        """`POST /krstock/order/v1/modify`(공식 openapi.json 확인, 모듈
        docstring 참조). `cor_qty`/`cor_pr`가 필수 필드라 가격/수량을 모두
        요구한다 — 부분 정보만 주는 정정은 지원하지 않는다(fail-closed).

        kwargs 키 이름: 실제 호출부(src/services/order_service/modify.py)는
        `price`/`size`를 쓰고 Bitget도 `size`를 쓰지만, KIS는 `quantity`를
        쓴다(레포 내 기존 불일치) — 이 어댑터는 실제 호출부와 맞추기 위해
        `size`를 우선하고 `quantity`도 하위호환으로 받는다.

        정정 후 새 `mkt_orr_no`가 발급되므로(응답 확인) 그것으로
        `exchange_order_id`를 갱신해 반환한다. `get_order()`가 구조적으로
        불가능해(모듈 docstring 참조) 재조회 없이 직접 Order를 구성한다 —
        `side`/AIOS 전용 필드(client_order_id 등)는 자리표시자이며, 실제
        호출부는 이 반환값에서 `exchange_order_id`/`status`만 읽고 나머지는
        원본 DB 행을 유지한다(order_service/modify.py 확인)."""
        symbol, mkt_orr_no = _split_exchange_order_id(order_id)
        if "price" not in kwargs or ("size" not in kwargs and "quantity" not in kwargs):
            raise FatalExchangeError(
                "NH modify_order는 price와 size(또는 quantity)를 모두 요구함"
                "(공식 openapi.json — cor_qty/cor_pr 모두 필수 필드로 확인)"
            )
        quantity = kwargs["size"] if "size" in kwargs else kwargs["quantity"]
        body = {
            "act_no": self._act_no,
            "org_mkt_orr_no": _parse_mkt_orr_no(mkt_orr_no),
            "all_pat_dit_cd": "1",  # 1=전체(잔량) 정정만 지원 — 부분 정정은 미구현
            "iem_cd": symbol,
            "cor_qty": str(quantity),
            "cor_pr": str(kwargs["price"]),
            "sop_cnd_pr": "0",  # 스톱지정가(16) 정정이 아니면 0
            "rmt_mkt_cd": _MARKET_CODE,
            "sor_mkt_sli_yn": "N",
        }
        raw = await self._request("POST", "/krstock/order/v1/modify", body=body)
        try:
            new_mkt_orr_no = str(raw["Output_0"]["mkt_orr_no"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH 정정 응답에 예상 필드 없음(공식 openapi.json 기준 mkt_orr_no "
                f"필요): {exc}"
            ) from exc
        now = datetime.now(timezone.utc)
        return Order(
            order_id=uuid4(),
            exchange_order_id=f"{symbol}:{new_mkt_orr_no}",
            client_order_id="",
            strategy_id="",
            strategy_version="",
            symbol=symbol,
            exchange="nh",
            side=OrderSide.BUY,  # placeholder — 호출부가 원본 side를 유지함(위 docstring)
            order_type=OrderType.LIMIT,
            quantity=Decimal(str(quantity)),
            status=OrderStatus.ACKNOWLEDGED,
            filled_quantity=Decimal("0"),
            created_at=now,
            updated_at=now,
            asset_class=AssetClass.KR_EQUITY,
        )

    async def get_order(self, order_id: str) -> Order:
        """모듈 docstring 참조 — 공식 openapi.json으로 확인한 구조적
        불일치(주문조회 응답에 우리 exchange_order_id 체계인 mkt_orr_no가
        없음) 때문에 근거 있는 구현이 불가능하다. 추측으로 잘못된 주문
        상태를 만드는 것보다 명시적 미구현이 안전하다(PM 배정 지침 (2))."""
        raise NotImplementedError(
            "NHAdapter.get_order: dailyOrderExecution 응답에 mkt_orr_no(당사 "
            "exchange_order_id 체계)를 조회할 필드가 없음이 공식 openapi.json으로 "
            "확인됨(itg_orr_no만 존재, 02e 스펙 §3 참조) — 라이브 계좌로 두 "
            "식별자의 매핑 관계를 확인하기 전까지 구현 보류"
        )

    async def health_check(self: _BalanceReadingClient) -> bool:
        try:
            await self.get_balance()
            return True
        except Exception:  # noqa: BLE001 — 헬스체크는 어떤 예외든 False로 수렴
            return False
