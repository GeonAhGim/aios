"""scripts/kis_tr_parse.py 단위 테스트 — BR-11(ADR-2026-09-06-I D7).

순수 함수만 다룬다(네트워크 없음). 공식 저장소 예제의 세 가지 실제 구조를 합성
데이터로 재현한다: REST GET(조회), REST POST(주문, 실전/모의 tr_id 분기), WebSocket
(체결/호가 등, `columns=[...]`로 필드가 코드에 그대로 남는 유일한 경우).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kis_tr_parse = _load_module("kis_tr_parse", SCRIPTS_DIR / "kis_tr_parse.py")


_REST_GET_FILE = '''
##############################################################################################
# [국내주식] 기본시세 > 주식현재가 시세[v1_국내주식-008]
##############################################################################################

API_URL = "/uapi/domestic-stock/v1/quotations/inquire-price"

def inquire_price(
    env_dv: str,  # [필수] 실전모의구분
    fid_cond_mrkt_div_code: str,  # [필수] 시장분류코드
    fid_input_iscd: str,  # [필수] 종목코드
    tr_cont: str = "",  # 연속거래여부
) -> None:
    """시세 조회."""
    tr_id = "FHKST01010100"

    params = {
        "FID_COND_MRKT_DIV_CODE": fid_cond_mrkt_div_code,  # 시장분류코드
        "FID_INPUT_ISCD": fid_input_iscd,  # 종목코드
        "TR_CONT": tr_cont,
    }

    res = ka._url_fetch(API_URL, tr_id, tr_cont, params)
    if res.isOK():
        current_data = pd.DataFrame([res.getBody().output])
        return current_data
'''

_REST_POST_ORDER_FILE = '''
##############################################################################################
# [국내주식] 주문/계좌 > 주식주문(현금) [v1_국내주식-001]
##############################################################################################

API_URL = "/uapi/domestic-stock/v1/trading/order-cash"

def order_cash(
    env_dv: str,  # [필수] 실전모의구분
    ord_dv: str,  # [필수] 매도매수구분
    cano: str,  # [필수] 종합계좌번호
    pdno: str,  # [필수] 상품번호
    sll_type: str = "",  # 매도유형
) -> None:
    if env_dv == "real":
        if ord_dv == "sell":
            tr_id = "TTTC0011U"
        else:
            tr_id = "TTTC0012U"
    else:
        if ord_dv == "sell":
            tr_id = "VTTC0011U"
        else:
            tr_id = "VTTC0012U"

    params = {
        "CANO": cano,  # 종합계좌번호
        "PDNO": pdno,  # 상품번호
        "SLL_TYPE": sll_type,  # 매도유형
    }

    res = ka._url_fetch(API_URL, tr_id, "", params, postFlag=True)
    if res.isOK():
        current_data = pd.DataFrame([res.getBody().output])
        return current_data
'''

_WEBSOCKET_FILE = '''
##############################################################################################
# [국내선물옵션] 실시간시세 > 상품선물 실시간체결가[실시간-022]
##############################################################################################

def commodity_futures_realtime_conclusion(
        tr_type: str,  # [필수] 구독 등록/해제 여부
        tr_key: str,  # [필수] 종목코드
) -> None:
    tr_id = "H0CFCNT0"

    params = {
        "tr_key": tr_key,
    }

    msg = ka.data_fetch(tr_id, tr_type, params)

    columns = [
        "futs_shrn_iscd",
        "bsop_hour",
        "futs_prpr",
    ]

    return msg, columns
'''

_NO_PATTERN_FILE = '''
# [인증] 접근토큰발급[인증-001]
def auth_token(appkey: str, appsecret: str) -> None:
    tr_id = "AUTHTKN1"
    return requests.post("https://openapi.koreainvestment.com/oauth2/tokenP")
'''


def test_extract_request_meta_rest_get() -> None:
    meta = kis_tr_parse.extract_request_meta(_REST_GET_FILE)

    assert meta.path == "/uapi/domestic-stock/v1/quotations/inquire-price"
    assert meta.method == "GET"
    assert meta.error is None
    by_name = {p.name: p.required for p in meta.params}
    assert by_name["FID_COND_MRKT_DIV_CODE"] is True
    assert by_name["FID_INPUT_ISCD"] is True
    assert by_name["TR_CONT"] is False


def test_extract_request_meta_rest_post_with_postflag() -> None:
    meta = kis_tr_parse.extract_request_meta(_REST_POST_ORDER_FILE)

    assert meta.path == "/uapi/domestic-stock/v1/trading/order-cash"
    assert meta.method == "POST"
    assert meta.error is None
    by_name = {p.name: p.required for p in meta.params}
    assert by_name["CANO"] is True
    assert by_name["PDNO"] is True
    assert by_name["SLL_TYPE"] is False


def test_extract_request_meta_websocket_has_no_path() -> None:
    meta = kis_tr_parse.extract_request_meta(_WEBSOCKET_FILE)

    assert meta.path is None
    assert meta.method == "WS"
    assert meta.error is None
    assert [p.name for p in meta.params] == ["tr_key"]
    assert meta.params[0].required is True


def test_extract_request_meta_reports_error_when_no_pattern_matches() -> None:
    meta = kis_tr_parse.extract_request_meta(_NO_PATTERN_FILE)

    assert meta.path is None
    assert meta.method is None
    assert meta.error is not None
    assert "API_URL" in meta.error


def test_extract_response_meta_rest_reports_output_container() -> None:
    meta = kis_tr_parse.extract_response_meta(_REST_GET_FILE)

    assert meta.kind == "rest"
    assert meta.containers == ("output",)
    assert meta.fields == ()
    assert meta.error is None


def test_extract_response_meta_websocket_reports_column_fields() -> None:
    meta = kis_tr_parse.extract_response_meta(_WEBSOCKET_FILE)

    assert meta.kind == "ws"
    assert meta.fields == ("futs_shrn_iscd", "bsop_hour", "futs_prpr")
    assert meta.containers == ()
    assert meta.error is None


def test_extract_response_meta_reports_error_without_output_access() -> None:
    meta = kis_tr_parse.extract_response_meta(_NO_PATTERN_FILE)

    assert meta.kind == "rest"
    assert meta.containers == ()
    assert meta.error is not None


def test_parse_def_args_splits_required_and_optional() -> None:
    required, optional = kis_tr_parse.parse_def_args(_REST_POST_ORDER_FILE)

    assert required == frozenset({"env_dv", "ord_dv", "cano", "pdno"})
    assert optional == frozenset({"sll_type"})


def test_extract_trs_from_text_shares_request_meta_across_tr_ids() -> None:
    rows = kis_tr_parse.extract_trs_from_text(
        _REST_POST_ORDER_FILE, "examples_llm/domestic_stock/order_cash/order_cash.py"
    )

    assert [r["tr_id"] for r in rows] == ["TTTC0011U", "TTTC0012U", "VTTC0011U", "VTTC0012U"]
    for row in rows:
        assert row["path"] == "/uapi/domestic-stock/v1/trading/order-cash"
        assert row["method"] == "POST"
        assert row["extraction_error"] is None
        assert row["domain"] == "domestic_stock"


def test_extract_trs_from_text_no_tr_id_returns_empty() -> None:
    assert kis_tr_parse.extract_trs_from_text("def main():\n    pass\n", "x/y/z.py") == []


def test_extract_trs_from_text_surfaces_extraction_error_not_silently() -> None:
    rows = kis_tr_parse.extract_trs_from_text(_NO_PATTERN_FILE, "examples_llm/auth/auth_token.py")

    assert len(rows) == 1
    assert rows[0]["path"] is None
    assert rows[0]["method"] is None
    assert rows[0]["extraction_error"]  # 조용히 비어있지 않고 사유가 남아야 한다


def test_extract_trs_from_text_is_deterministic() -> None:
    first = kis_tr_parse.extract_trs_from_text(
        _WEBSOCKET_FILE,
        "examples_llm/domestic_futureoption/commodity_futures_realtime_conclusion/x.py",
    )
    second = kis_tr_parse.extract_trs_from_text(
        _WEBSOCKET_FILE,
        "examples_llm/domestic_futureoption/commodity_futures_realtime_conclusion/x.py",
    )

    assert first == second
