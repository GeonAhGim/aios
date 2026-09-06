"""task-1983 BR-7 결함 수정(review task-1978 REJECT 후속) — 해외선물옵션 5개 TR
(tr_id, http method, path)를 `docs/design/kis_tr_reference.json` 원문과 기계
대조한다.

리뷰가 지적한 결함(옵션시세 HHDFO55010000의 path가 opt-price가 아니라
inquire-price로 오배선, 잔고 OTFM1412R의 path가 inquire-unpd가 아니라
inquire-balance로 오배선)는 mock 고정값 대조로는 잡히지 않았다(고정값 자체를
잘못 맞춰 넣으면 통과해버림) — 그래서 여기서는 기준 목록 원문을 파싱해
`tests/fixtures/kis/generated_cases.py`(BR-13)가 쓰는 것과 동일한
`load_reference_rows()`로 대조한다.

DoD(c) 모의투자 분기 회귀: 이 도메인 5개 TR은 전부 O/H 접두이고
`kis_tr_reference.json`의 overseas_futureoption 35개 전체를 뒤져도 V-접두
대응 TR이 하나도 없다(overseas_futureoption_mixin.py 모듈 docstring 참조,
구조적 증거·미검증) — 국내 T/J/C→V 치환 규칙(adapter.py
`_PAPER_SWAP_PREFIXES`)이 이 도메인에는 적용되면 안 된다. is_paper_trading=
True로 호출해도 tr_id가 원문 그대로 실려야 하고, 치환 규칙에 "O"나 "H"를
잘못 추가하면 이 단언이 깨진다는 것을 별도 테스트로 실증한다.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest

import src.exchanges.kis.adapter as adapter_module
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import KISAdapter
from tests.fixtures.kis.generated_cases import load_reference_rows

_REFERENCE = load_reference_rows()

_QUOTE_FUTURES_TR = "HHDFC55010000"
_QUOTE_OPTION_TR = "HHDFO55010000"
_ORDER_TR = "OTFM3001U"
_CANCEL_TR = "OTFM3003U"
_BALANCE_TR = "OTFM1412R"
_ALL_TRS = (_QUOTE_FUTURES_TR, _QUOTE_OPTION_TR, _ORDER_TR, _CANCEL_TR, _BALANCE_TR)


def _reference_row(tr_id: str) -> dict[str, Any]:
    return _REFERENCE[tr_id]


def _futures_order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="ESZ26",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.OVERSEAS_FUTURES,
        contract_multiplier=Decimal("50"),
        underlying_symbol="ES",
    )


_QUOTE_PATHS = (_reference_row(_QUOTE_FUTURES_TR)["path"], _reference_row(_QUOTE_OPTION_TR)["path"])
_ORDER_PATHS = (_reference_row(_ORDER_TR)["path"], _reference_row(_CANCEL_TR)["path"])
_BALANCE_PATH = _reference_row(_BALANCE_TR)["path"]


def _make_paper_adapter(captured: list[httpx.Request]) -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})
        captured.append(request)
        if path in _QUOTE_PATHS:
            output = {"last": "4521.50", "tvol": "12345"}
        elif path in _ORDER_PATHS:
            output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        else:
            output = {}
        payload: dict[str, object] = {"rt_cd": "0", "msg1": "OK", "output": output}
        if path == _BALANCE_PATH:
            payload["output1"] = []
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def test_all_five_trs_have_reference_rows() -> None:
    """DoD(b) — 이 mixin이 조립하는 5개 TR은 전부 기준 목록 원문에 실존해야
    한다(추정 경로 사용 금지의 전제조건)."""
    missing = [tr_id for tr_id in _ALL_TRS if tr_id not in _REFERENCE]
    assert missing == [], f"기준 목록에 없는 TR(추정 경로 사용 불가): {missing}"


async def test_get_futures_price_matches_reference() -> None:
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    row = _reference_row(_QUOTE_FUTURES_TR)

    await adapter.get_overseas_futureoption_price("ESZ26", is_option=False)

    request = captured[-1]
    assert request.method == row["method"]
    assert request.url.path == row["path"]
    assert request.headers["tr_id"] == _QUOTE_FUTURES_TR


async def test_get_option_price_matches_reference() -> None:
    """리뷰(task-1978)가 REJECT한 결함 1 — 수정 전에는 이 TR도 futures와 같은
    inquire-price path로 나가 FAIL했다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    row = _reference_row(_QUOTE_OPTION_TR)

    await adapter.get_overseas_futureoption_price("ESZ26C4500", is_option=True)

    request = captured[-1]
    assert request.method == row["method"]
    assert request.url.path == row["path"]
    assert request.headers["tr_id"] == _QUOTE_OPTION_TR


async def test_place_order_matches_reference() -> None:
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    row = _reference_row(_ORDER_TR)

    await adapter.place_overseas_futureoption_order(_futures_order())

    request = captured[-1]
    assert request.method == row["method"]
    assert request.url.path == row["path"]
    assert request.headers["tr_id"] == _ORDER_TR


async def test_cancel_order_matches_reference() -> None:
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    row = _reference_row(_CANCEL_TR)

    await adapter.cancel_overseas_futureoption_order("ORG:1", quantity=Decimal("1"))

    request = captured[-1]
    assert request.method == row["method"]
    assert request.url.path == row["path"]
    assert request.headers["tr_id"] == _CANCEL_TR


async def test_get_balance_matches_reference() -> None:
    """리뷰(task-1978)가 REJECT한 결함 2 — 수정 전에는 이 TR이 inquire-balance
    path로 나가 FAIL했다(원문은 inquire-unpd)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    row = _reference_row(_BALANCE_TR)

    await adapter.get_overseas_futureoption_balance()

    request = captured[-1]
    assert request.method == row["method"]
    assert request.url.path == row["path"]
    assert request.headers["tr_id"] == _BALANCE_TR


async def test_paper_mode_tr_ids_unchanged_from_reference() -> None:
    """DoD(c) — 이 도메인은 V-접두 대응 TR이 없으므로 is_paper_trading=True여도
    5개 TR id가 원문과 동일해야 한다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    await adapter.get_overseas_futureoption_price("ESZ26", is_option=False)
    await adapter.get_overseas_futureoption_price("ESZ26C4500", is_option=True)
    await adapter.place_overseas_futureoption_order(_futures_order())
    await adapter.cancel_overseas_futureoption_order("ORG:1", quantity=Decimal("1"))
    await adapter.get_overseas_futureoption_balance()

    seen_tr_ids = [request.headers["tr_id"] for request in captured]
    assert seen_tr_ids == list(_ALL_TRS)


async def test_paper_swap_rule_mutation_breaks_tr_id_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DoD(c) 실증 — `_PAPER_SWAP_PREFIXES`에 O/H를 잘못 추가하면(한 글자
    변경) 위 항등 단언이 깨진다는 것을 직접 보인다(국내 V-치환 규칙을 이
    O/H 도메인에 무단 적용하면 안 되는 이유)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    monkeypatch.setattr(adapter_module, "_PAPER_SWAP_PREFIXES", ("T", "J", "C", "H", "O"))

    await adapter.get_overseas_futureoption_price("ESZ26", is_option=False)

    request = captured[-1]
    # 원문 tr_id는 HHDFC55010000이지만, 치환 규칙이 H를 포함하면 VHDFC55010000로
    # 잘못 바뀐다 — 즉 정상 규칙이라면 통과할 identity 단언이 깨진다.
    assert request.headers["tr_id"] != _QUOTE_FUTURES_TR
    assert request.headers["tr_id"] == "VHDFC55010000"
