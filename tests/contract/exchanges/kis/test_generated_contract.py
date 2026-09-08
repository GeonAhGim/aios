"""BR-13(task-1930) 계약 테스트 -- BR-12(task-1929) 생성 KIS mixin 전수.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §10 BR,
ADR-2026-09-06-I D7. 모의투자 계좌는 사람만 만들 수 있다(HB-3) -- 계정 없이도
정확성을 증명하기 위해 고정 픽스처(`tests/fixtures/kis/generated_cases.py`)로
`src/exchanges/kis/generated/*.py`의 전 TR을 순회하며 두 가지만 확인한다:

1. 요청 조립 -- 생성 소스가 실제로 내보내는 (http 메서드, path, tr_id)가
   `docs/design/kis_tr_reference.json`(BR-11, 공식 저장소 기계 추출본)과
   바이트 동일한지, 그리고 실행 시점에 그 값 그대로 헤더/쿼리·바디에
   실리는지.
2. 응답 왕복 -- 생성 코드는 응답을 파싱하지 않으므로(BR-12 결정, D7)
   "파싱"의 계약은 곧 항등 왕복이다: `_request`가 뭘 반환하든 그대로
   돌려주는지.

핸드라이팅 mixin(trading_mixin.py 등)은 범위 밖 -- 이미 자체 단위/통합
테스트가 있다. 실계좌(모의투자 서버) 왕복 검증도 범위 밖 -- BR-14
(task-1931, tests/integration/exchanges/kis/)가 HB-3 해소 후 담당한다.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.exchanges.kis.adapter import REAL_BASE_URL, KISAdapter
from tests.fixtures.kis.generated_cases import (
    GeneratedCase,
    discover_generated_cases,
    load_reference_rows,
)

_REFERENCE = load_reference_rows()
_CASES = discover_generated_cases()
_REST_CASES = [c for c in _CASES if c.kind == "rest"]
_WS_CASES = [c for c in _CASES if c.kind == "ws"]

_TOKEN_RESPONSE = {
    "access_token": "tok-br13-fixture",
    "access_token_token_expired": "2099-01-01 00:00:00",
}


def _case_id(case: GeneratedCase) -> str:
    return case.tr_id


def _make_real_adapter(handler: Any) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url=REAL_BASE_URL, transport=transport)
    # is_paper_trading=True -- task-1975(BR-12 주문성 생성 메서드에
    # @require_paper_sandbox 부착, ADR-2026-08-29-E)가 걸린 뒤로 이 하드가드를
    # 통과하려면 계약 테스트 adapter도 PAPER/sandbox 구성이어야 한다(task-2018).
    # 모의투자 tr_id 치환(T/J/C -> V)은 여전히 이 테스트의 관심사가 아니다
    # (별도로 tests/unit/exchanges/test_kis_live_guard.py 등이 다룸) -- 그래서
    # 인스턴스 단위로 치환을 무력화해, 생성 코드가 스스로 조립한 tr_id/path가
    # 그대로 실리는지만(BR-11 문자 그대로 일치) 계속 검증한다.
    adapter = KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=client
    )
    adapter._resolve_tr_id = lambda tr_id: tr_id  # type: ignore[method-assign]
    return adapter


def _make_live_adapter(handler: Any) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url=REAL_BASE_URL, transport=transport)
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=False, http_client=client)


async def _assert_rest_contract(case: GeneratedCase) -> None:
    row = _REFERENCE[case.tr_id]
    assert case.http_method == row["method"], f"{case.tr_id}: http 메서드가 BR-11 기준과 다름"
    assert case.path == row["path"], f"{case.tr_id}: path가 BR-11 기준과 다름"

    sample_params = {p["name"]: f"V_{p['name']}" for p in row["params"]}
    containers = row["response"]["containers"] or ["output"]
    canned_response = {
        "rt_cd": "0",
        "msg1": "OK",
        **{c: {"marker": f"{case.tr_id}-fixture"} for c in containers},
    }
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        captured.append(request)
        return httpx.Response(200, json=canned_response)

    adapter = _make_real_adapter(handler)
    try:
        method = getattr(adapter, case.method_name)
        result = await method(sample_params)
    finally:
        await adapter.aclose()

    assert len(captured) == 1, f"{case.tr_id}: _request가 정확히 1회 호출되지 않음"
    request = captured[0]
    assert request.method == row["method"], f"{case.tr_id}: 실행 시점 http 메서드 불일치"
    assert request.url.path == row["path"], f"{case.tr_id}: 실행 시점 path 불일치"
    assert request.headers["tr_id"] == case.tr_id, f"{case.tr_id}: tr_id 헤더 불일치"
    assert request.headers["custtype"] == "P"
    assert "authorization" in request.headers

    if case.arg_style == "params":
        assert dict(request.url.params) == sample_params, f"{case.tr_id}: 쿼리 파라미터 불일치"
    else:
        assert json.loads(request.content) == sample_params, f"{case.tr_id}: 요청 바디 불일치"

    assert result == canned_response, f"{case.tr_id}: 응답이 파싱 없이 그대로 왕복하지 않음"


async def _noop_callback(_raw: str) -> None:
    return None


async def _assert_ws_contract(case: GeneratedCase, monkeypatch: pytest.MonkeyPatch) -> None:
    row = _REFERENCE[case.tr_id]
    assert row["method"] == "WS", f"{case.tr_id}: BR-11 기준이 WS가 아님"

    module = importlib.import_module(case.module_name)
    cls = getattr(module, case.class_name)
    captured: dict[str, Any] = {}

    async def _fake_subscription(
        url: str, subscribe_msg: dict[str, Any], _on_frame: Any, **_kw: Any
    ) -> None:
        captured["url"] = url
        captured["subscribe_msg"] = subscribe_msg

    monkeypatch.setattr(module, "_run_kis_ws_subscription", _fake_subscription)

    class _FakeWsHost:
        _is_paper_trading = False

        async def get_ws_approval_key(self) -> str:
            return "approval-key-fixture"

    method = getattr(cls, case.method_name)
    await method(_FakeWsHost(), tr_key="TESTKEY0001", callback=_noop_callback)

    assert "subscribe_msg" in captured, f"{case.tr_id}: 구독 메시지가 조립되지 않음"
    body_input = captured["subscribe_msg"]["body"]["input"]
    assert body_input["tr_id"] == case.tr_id, f"{case.tr_id}: 구독 메시지 tr_id 불일치"
    assert body_input["tr_key"] == "TESTKEY0001", f"{case.tr_id}: 구독 메시지 tr_key 전달 안 됨"
    assert captured["url"] == module.WS_REAL_URL, (
        f"{case.tr_id}: is_paper_trading=False인데 실전 URL 아님"
    )


def test_discovers_all_generated_methods() -> None:
    """발견 로직이 조용히 깨져 케이스가 사라지는 회귀를 잡는다(BR-12 생성
    당시 289건 + 이후 변동분). REST/WS 분류가 서로 배타적인지도 함께 확인."""
    assert len(_CASES) >= 289
    assert len(_REST_CASES) + len(_WS_CASES) == len(_CASES)
    assert {c.tr_id for c in _CASES} <= set(_REFERENCE)


@pytest.mark.parametrize("case", _REST_CASES, ids=_case_id)
async def test_rest_case_request_and_response_roundtrip(case: GeneratedCase) -> None:
    await _assert_rest_contract(case)


@pytest.mark.parametrize("case", _WS_CASES, ids=_case_id)
async def test_ws_case_subscribes_with_correct_tr_id(
    case: GeneratedCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _assert_ws_contract(case, monkeypatch)


async def test_contract_helper_detects_mismatched_path() -> None:
    """DoD -- 파라미터 하나를 바꾸면 테스트가 실패함을 증명. 정상 케이스의
    path를 하나 조작하면 `_assert_rest_contract`가 즉시 잡아야 한다(테스트가
    실제로 뭔가를 검증하고 있다는 근거)."""
    case = _REST_CASES[0]
    assert case.path is not None, "REST case는 path가 None일 수 없음"
    mutated = replace(case, path=case.path + "-broken")
    with pytest.raises(AssertionError):
        await _assert_rest_contract(mutated)


async def test_rest_case_rejected_when_adapter_is_live() -> None:
    """DoD(task-2018) -- 하드가드가 실제로 살아 있다는 증거. task-1975가
    STTN1101U(주문성 생성 메서드)에 붙인 `@require_paper_sandbox`는
    LIVE로 구성된(is_paper_trading=False) adapter의 호출을 반드시 막아야
    한다(ADR-2026-08-29-E) -- 이 negative 테스트가 없으면 위 픽스처가
    PAPER로 바뀐 것이 가드를 우회한 결과인지, 가드를 실제로 통과한
    결과인지 구별할 수 없다."""
    case = next(c for c in _REST_CASES if c.tr_id == "STTN1101U")
    row = _REFERENCE[case.tr_id]
    sample_params = {p["name"]: f"V_{p['name']}" for p in row["params"]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK"})

    adapter = _make_live_adapter(handler)
    try:
        method = getattr(adapter, case.method_name)
        with pytest.raises(FrozenZonePaperAdapterBlockedError):
            await method(sample_params)
    finally:
        await adapter.aclose()


async def test_contract_helper_detects_response_tampering(monkeypatch: pytest.MonkeyPatch) -> None:
    """DoD -- 응답 항등 왕복 검사가 실제로 뭔가를 잡는지 증명. 생성 메서드가
    응답을 조용히 변형하는 결함을 흉내내면(monkeypatch) 실패해야 한다."""
    case = _REST_CASES[0]
    module = importlib.import_module(case.module_name)
    cls = getattr(module, case.class_name)
    original = getattr(cls, case.method_name)

    async def _tampering_method(self: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result = await original(self, params)
        return {**result, "output": {"tampered": True}}

    monkeypatch.setattr(cls, case.method_name, _tampering_method)
    with pytest.raises(AssertionError):
        await _assert_rest_contract(case)
