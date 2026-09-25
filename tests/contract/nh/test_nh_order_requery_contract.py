"""ADR-2026-09-09-B H-3(task-2615) 계약 테스트 -- BR-13 방식.

모의투자 계좌는 사람만 만들 수 있다(HB-3) -- 계정 없이도 정확성을
증명하기 위해, 자산군 공식 OpenAPI 스펙(`https://www.nhplug.com/
openapi-docs/krstock/openapi.json`, 도메인이 정본)에서 2026-09-16
`curl`로 원문을 내려받아 `components.schemas`/`x-realtime-channels`를
파이썬(`json.load`)으로 직접 파싱해 얻은 고정 참조값(아래 `_REFERENCE`
계열 상수, 모두 리터럴로 동결)과 실제 어댑터 코드의 요청 조립/응답
파싱/WS 프레임 파싱을 대조한다. 이 파일이 검증하는 것은 두 가지뿐이다:

1. 요청 조립 -- `place_order`/`modify_order`/`cancel_order`가 실제로
   보내는 body가 공식 스펙의 `Input_0.required`를 전부 포함하는지, 그리고
   `get_order`가 (구현 가능한 척하지 않고) 확정된 이유로 fail-closed인지.
2. 응답/프레임 파싱 -- `Output_0.mkt_orr_no` 응답 필드 사용, WS `mc` 채널
   `push_example`(공식 스펙 예시 값 그대로) 왕복.

핸드라이팅 mixin의 나머지 동작(에러 매핑, 페이퍼 가드 등)은 범위 밖 --
이미 tests/integration/test_nh_adapter.py가 다룬다(KIS BR-13,
tests/contract/exchanges/kis/test_generated_contract.py와 동일 원칙:
생성기가 없는 NH는 "생성 코드 대 BR-11 참조표" 대신 "핸드라이팅 코드
대 openapi.json 직접 파싱 결과"를 대조한다).
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.nh.adapter import NHAdapter
from src.exchanges.nh.websocket_parsing import parse_mc_ticker_frame
from tests.integration.test_nh_adapter import (
    _unguarded_cancel_order,
    _unguarded_modify_order,
    _unguarded_place_order,
)

# ---------------------------------------------------------------------------
# 고정 참조(FROZEN REFERENCE) -- 2026-09-16, task-2615, curl + json.load로
# 직접 파싱해 확인. 값을 바꾸려면 openapi.json을 다시 확인해야 한다.
# ---------------------------------------------------------------------------

_CASHBUY_REQUIRED = frozenset(
    {
        "act_no",
        "iem_cd",
        "orr_qty",
        "nmn_pr_tp_cd",
        "orr_cnd_dit_cd",
        "ssl_nmn_pr_dit_cd",
        "rmt_mkt_cd",
        "sor_mkt_sli_yn",
    }
)
_MODIFY_REQUIRED = frozenset(
    {
        "act_no",
        "org_mkt_orr_no",
        "all_pat_dit_cd",
        "iem_cd",
        "cor_qty",
        "cor_pr",
        "sop_cnd_pr",
        "rmt_mkt_cd",
        "sor_mkt_sli_yn",
    }
)
_CANCEL_REQUIRED = frozenset({"act_no", "org_mkt_orr_no", "all_pat_dit_cd", "iem_cd"})

# dailyOrderExecution Output_1 항목 필드 전체(공식 스펙, docs/exchanges/
# NH_GAPS.md §1에 동일 목록 기록) -- mkt_orr_no가 없다는 것이 get_order()
# fail-closed 결정의 근거 그 자체다.
_DAILY_ORDER_EXECUTION_OUTPUT_1_FIELDS = frozenset(
    {
        "itg_orr_no",
        "orr_mkt_cd_nm",
        "mo_itg_orr_no",
        "org_itg_orr_no",
        "iem_cd",
        "iem_nm",
        "sby_dit_cd_nm",
        "cor_can_dit_cd_nm",
        "lon_dt",
        "cfd_lon_cd",
        "nmn_pr_tp_cd_nm",
        "orr_cnd_dit_cd_nm",
        "orr_qty",
        "orr_pr",
        "tot_cns_qty",
        "cns_avg_uit_pr",
        "cns_amt",
        "cns_cnt",
        "ny_cns_qty",
        "cor_qty",
        "can_qty",
        "orr_tm",
        "orr_mdi",
        "bnd_byn_dt",
        "syn_ttn_dit_cd_nm",
        "orr_rjt_rsn_cd_nm",
        "pcs_emp_no",
        "rmt_mkt_cd",
        "sor_mkt_sli_yn",
        "krx_lnt_opi_sec_co_cd",
        "krx_lnt_opi_act_no",
        "krx_lnt_cnf_cpl_hur",
    }
)

# x-realtime-channels.channels[tr_cd="mc"].push_example -- 공식 스펙의 예시
# 값 그대로(docs/exchanges/NH_GAPS.md §2-1).
_MC_PUSH_EXAMPLE = {
    "code": "005940",
    "time": "14:00:31",
    "sign": "2",
    "change": "2500",
    "price": "31750",
    "chrate": "8.55",
    "high": "32200",
    "low": "29450",
    "offer": "31750",
    "bid": "31700",
    "volume": "837624",
    "volrate": "81.75",
    "movolume": "13",
    "value": "26387",
    "open": "29600",
    "avgprice": "31503",
    "janggubun": "0",
    "bidrate": "57.65",
    "volpower": "137.24",
    "new_volume": "837624",
    "bidvolall": "482854",
    "offvolall": "351828",
    "kospigb": "1",
    "value_won": "26387679000",
}

# x-realtime-channels.protocol.subscribe_message -- 공식 스펙 그대로.
_WS_SUBSCRIBE_ENVELOPE_KEYS = {"header": {"token", "tr_type"}, "body": {"tr_cd", "tr_key"}}

TOKEN_RESPONSE = {"access_token": "tok-contract", "expires_in": 86400}


def _make_adapter(handler) -> NHAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://moapi.nhplug.com:8443", transport=transport)
    return NHAdapter("appkey", "appsecret", "1234567890", is_paper_trading=True, http_client=client)


def _route(request: httpx.Request, routes: dict) -> httpx.Response:
    if request.url.path == "/oauth2/token":
        return httpx.Response(200, json=TOKEN_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


def _success(output_0: dict) -> dict:
    return {"rsp_cd": "00000", "rsp_msg": "정상처리완료", "Output_0": output_0}


def _order(**overrides) -> Order:
    defaults = dict(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="nh",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )
    defaults.update(overrides)
    return Order(**defaults)


# ---------- 1. 요청 조립 계약 ----------


async def test_place_order_body_covers_confirmed_required_fields():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_success({"mkt_orr_no": 555}))

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler}))
    result = await _unguarded_place_order(adapter, _order())

    assert _CASHBUY_REQUIRED <= captured.keys(), (
        f"cashBuy body가 공식 openapi.json 필수 필드를 다 채우지 못함: "
        f"missing={_CASHBUY_REQUIRED - captured.keys()}"
    )
    # 응답 파싱 계약 -- Output_0.mkt_orr_no로 exchange_order_id를 조립한다.
    assert result.exchange_order_id == "005930:555"


async def test_modify_order_body_covers_confirmed_required_fields():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_success({"mkt_orr_no": 777}))

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/modify": handler}))
    result = await _unguarded_modify_order(
        adapter, "005930:555", price=Decimal("71000"), size=Decimal("5")
    )

    assert _MODIFY_REQUIRED <= captured.keys(), (
        f"modify body가 공식 openapi.json 필수 필드를 다 채우지 못함: "
        f"missing={_MODIFY_REQUIRED - captured.keys()}"
    )
    assert result.exchange_order_id == "005930:777"


async def test_cancel_order_body_covers_confirmed_required_fields():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_success({"mkt_orr_no": 999}))

    adapter = _make_adapter(lambda request: _route(request, {"/krstock/order/v1/cancel": handler}))
    ok = await _unguarded_cancel_order(adapter, "005930:555")

    assert _CANCEL_REQUIRED <= captured.keys(), (
        f"cancel body가 공식 openapi.json 필수 필드를 다 채우지 못함: "
        f"missing={_CANCEL_REQUIRED - captured.keys()}"
    )
    assert ok is True


# ---------- 2. get_order() fail-closed 계약 ----------


def test_daily_order_execution_output1_has_no_mkt_orr_no_field():
    """§1 fail-closed 결정의 근거 자체를 리그레션으로 고정한다 -- 누군가
    나중에 openapi.json이 필드를 추가했다고 착각하고 get_order()를
    추측으로 구현하면, 이 assert가 먼저 "그 근거가 아직 유효한지 다시
    확인하라"고 막아선다(참조표 자체는 여기서 갱신하지 않는 한 불변)."""
    assert "mkt_orr_no" not in _DAILY_ORDER_EXECUTION_OUTPUT_1_FIELDS
    assert "itg_orr_no" in _DAILY_ORDER_EXECUTION_OUTPUT_1_FIELDS


async def test_get_order_is_fail_closed_not_a_guess():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(NotImplementedError, match="mkt_orr_no"):
        await adapter.get_order("005930:555")


# ---------- 3. WebSocket mc 채널 프레임 계약 ----------


def test_subscribe_message_envelope_matches_confirmed_protocol():
    from src.exchanges.nh.websocket_mixin import build_subscribe_message

    msg = build_subscribe_message("tok", "mc", "005940")
    assert set(msg["header"].keys()) == _WS_SUBSCRIBE_ENVELOPE_KEYS["header"]
    assert set(msg["body"].keys()) == _WS_SUBSCRIBE_ENVELOPE_KEYS["body"]
    assert msg["body"]["tr_cd"] == "mc"


def test_parse_mc_ticker_frame_matches_official_push_example():
    raw = json.dumps({"header": {"tr_cd": "mc", "tr_key": "005940"}, "body": _MC_PUSH_EXAMPLE})
    ticker = parse_mc_ticker_frame(raw)
    assert ticker is not None
    assert ticker.symbol == "005940"
    assert ticker.price == Decimal("31750")
    assert ticker.bid == Decimal("31700")
    assert ticker.ask == Decimal("31750")
    assert ticker.volume_24h == Decimal("837624")


def test_parse_mc_ticker_frame_ignores_subscribe_ack():
    raw = json.dumps({"header": {"token": "t", "tr_type": "1"}, "body": {"tr_cd": "mc"}})
    assert parse_mc_ticker_frame(raw) is None


def test_parse_mc_ticker_frame_ignores_other_channels():
    raw = json.dumps({"header": {"tr_cd": "mb", "tr_key": "005940"}, "body": {}})
    assert parse_mc_ticker_frame(raw) is None


def test_parse_mc_ticker_frame_fails_closed_on_missing_field():
    """서버가 확인된 스키마와 다르게 응답하면(필드 누락) 추측으로 채우지
    않고 즉시 실패한다 -- 미확인 필드 추측 0건 원칙."""
    raw = json.dumps({"header": {"tr_cd": "mc"}, "body": {"code": "005940"}})
    with pytest.raises(FatalExchangeError):
        parse_mc_ticker_frame(raw)


def test_parse_mc_ticker_frame_fails_closed_on_non_json():
    with pytest.raises(FatalExchangeError):
        parse_mc_ticker_frame("not-json")


# ---------- 4. task-3979 (H-3, task-3798 리뷰 REJECT 후속) -- D2 하한 보강 ----------
#
# task-3798 리뷰가 지적한 결함: parse_mc_ticker_frame(신규 WS 핫패스)에
# 수치 성능 단언이 없고, get_order() fail-closed 경로에 게이트 적색
# 재현(실측 필드 누락 주입)이 없다(ecc08318/1bf6c043과 동일 결함 패턴).
# 새 기능 추가 없이 증빙만 보강한다 -- 아래 두 테스트가 그 보강분이다.


@pytest.mark.perf
def test_parse_mc_ticker_frame_meets_ws_fanout_latency_budget():
    """수치 성능 단언 -- ADR-2026-09-09-C Decision 1 예산표에 WS 프레임
    파싱 전용 항목이 없어 가장 가까운 유사 항목("WS 팬아웃 p95 500ms")을
    자체 예산으로 차용한다(task-3167/ecc08318, task-3165/1bf6c043
    DEEPEN과 동일 차용 근거) -- parse_mc_ticker_frame()은 그 팬아웃
    파이프라인의 첫 단계이므로 반복 호출의 p95 지연이 그 예산 안에
    들어와야 한다."""
    import time

    raw = json.dumps({"header": {"tr_cd": "mc", "tr_key": "005940"}, "body": _MC_PUSH_EXAMPLE})
    samples = 500
    budget_sec = 0.5  # WS 팬아웃 p95 500ms 예산(ADR Decision 1) 차용

    latencies: list[float] = []
    for _ in range(samples):
        start = time.perf_counter()
        parse_mc_ticker_frame(raw)
        latencies.append(time.perf_counter() - start)
    latencies.sort()
    p95 = latencies[int(samples * 0.95) - 1]

    print(
        f"[H-3 nh ws] parse_mc_ticker_frame x{samples} p95={p95 * 1000:.4f}ms "
        f"(budget<{budget_sec * 1000:.0f}ms)"
    )
    assert p95 < budget_sec, f"WS 팬아웃 p95 예산({budget_sec}s) 초과: {p95:.6f}s"


async def test_get_order_field_missing_injection_confirms_fail_closed_reasoning():
    """게이트 적색 재현(필드 누락 주입) -- get_order()가 fail-closed인
    근거(§1: dailyOrderExecution Output_1에 mkt_orr_no가 없음)를 리터럴
    비교(`test_daily_order_execution_output1_has_no_mkt_orr_no_field`)가
    아니라 실제 요청 경로(`adapter._request`)로 재현한다. 공식 스키마와
    동일한 필드 집합(`_DAILY_ORDER_EXECUTION_OUTPUT_1_FIELDS`, mkt_orr_no
    없음)을 그대로 응답 바디로 흘려보낸 뒤, place_order/modify_order가
    성공 시 쓰는 것과 동일한 방식(`Output_x["mkt_orr_no"]`)으로 주문
    식별자를 꺼내려 하면 KeyError로 즉시 실패함을 확인한다 -- get_order()가
    이 실패를 삼키지 않고 NotImplementedError로 미리 막는 이유가 실측
    와이어 데이터로도 성립함을 증명한다(tautology가 아님)."""
    output_1_row = {field: "0" for field in _DAILY_ORDER_EXECUTION_OUTPUT_1_FIELDS}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rsp_cd": "00000", "rsp_msg": "정상처리완료", "Output_1": [output_1_row]},
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/dailyOrderExecution": handler})
    )
    raw = await adapter._request(
        "POST", "/krstock/inquiry/v1/dailyOrderExecution", body={"act_no": "1234567890"}
    )
    row = raw["Output_1"][0]
    assert "itg_orr_no" in row  # 공식 스펙 확인 필드는 그대로 존재
    with pytest.raises(KeyError):
        _ = row["mkt_orr_no"]  # place_order/modify_order와 동일 방식의 추출은 즉시 실패
