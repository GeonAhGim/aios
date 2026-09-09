"""L4-31(task-2514) — `account_mode.account_aware_request()` 계약 테스트.

Bitget 실키(2026-09-09)가 Classic v2 엔드포인트에서 40085
("Unified Account mode, Classic Account API not supported")를 반환한
사건이 이 모듈의 근거다. 여기서는 실제 HTTP를 전혀 타지 않는 가짜
`_request` 클라이언트로 순수 상태 전이(CLASSIC -> UNIFIED)와 요청 재조립
로직만 고정 픽스처로 검증한다 — 어댑터 전체를 띄우는 계약 테스트는
`tests/unit/exchanges/bitget/test_uta_v3_dispatch.py`가 담당한다.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.bitget.account_mode import (
    BitgetAccountMode,
    RequestSpec,
    account_aware_request,
    requires_unified_switch,
)
from src.exchanges.bitget.error_codes import classify_body_code
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind, is_retryable


def _fatal_with_code(venue_code: str) -> FatalExchangeError:
    """실제 `_BitgetHTTPClient._request`가 하는 것과 동일하게
    `raise FatalExchangeError(...) from ExchangeError(...)` 체인을 흉내
    낸다 — `account_aware_request`는 `exc.__cause__`로 원인을 읽는다."""
    cause = ExchangeError(ExchangeErrorKind.AUTH, venue="bitget", venue_code=venue_code)
    fatal = FatalExchangeError(str(cause))
    fatal.__cause__ = cause
    return fatal


class _FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.account_mode = BitgetAccountMode.CLASSIC
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, path, params, body))
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return dict(result)


def _mode_switched_build(mode: BitgetAccountMode) -> RequestSpec:
    path = "/api/v2/x" if mode is BitgetAccountMode.CLASSIC else "/api/v3/x"
    body = {"size": "1"} if mode is BitgetAccountMode.CLASSIC else {"qty": "1"}
    return "POST", path, None, body


async def test_classic_mode_assembles_v2_request_without_retry() -> None:
    client = _FakeClient([{"code": "00000", "data": {}}])

    raw = await account_aware_request(client, _mode_switched_build)

    assert raw == {"code": "00000", "data": {}}
    assert client.calls == [("POST", "/api/v2/x", None, {"size": "1"})]
    assert client.account_mode is BitgetAccountMode.CLASSIC


async def test_preset_unified_mode_assembles_v3_request_without_retry() -> None:
    client = _FakeClient([{"code": "00000", "data": {}}])
    client.account_mode = BitgetAccountMode.UNIFIED

    raw = await account_aware_request(client, _mode_switched_build)

    assert raw == {"code": "00000", "data": {}}
    assert client.calls == [("POST", "/api/v3/x", None, {"qty": "1"})]


async def test_classic_mode_switches_to_unified_on_40085_and_retries_with_v3_shape() -> None:
    client = _FakeClient([_fatal_with_code("40085"), {"code": "00000", "data": {"ok": True}}])

    raw = await account_aware_request(client, _mode_switched_build)

    assert raw == {"code": "00000", "data": {"ok": True}}
    assert client.account_mode is BitgetAccountMode.UNIFIED
    assert client.calls == [
        ("POST", "/api/v2/x", None, {"size": "1"}),
        ("POST", "/api/v3/x", None, {"qty": "1"}),
    ]


async def test_unified_mode_40085_propagates_without_further_retry() -> None:
    """이미 UNIFIED로 확정된 상태에서 40085를 다시 받는 건 "재전환"이 아니라
    다른 문제(예: 카테고리 파라미터 누락)다 — 무한 재시도를 막기 위해
    CLASSIC 상태에서만 전환을 시도한다."""
    client = _FakeClient([_fatal_with_code("40085")])
    client.account_mode = BitgetAccountMode.UNIFIED

    with pytest.raises(FatalExchangeError):
        await account_aware_request(client, _mode_switched_build)

    assert client.calls == [("POST", "/api/v3/x", None, {"qty": "1"})]


async def test_classic_mode_unrelated_auth_error_does_not_switch_mode() -> None:
    """40012(서명 오류)는 계정 모드와 무관한 실패다 — UNIFIED로 잘못
    전환하면 다음 호출부터 엉뚱한 엔드포인트를 때리게 된다."""
    client = _FakeClient([_fatal_with_code("40012")])

    with pytest.raises(FatalExchangeError):
        await account_aware_request(client, _mode_switched_build)

    assert client.account_mode is BitgetAccountMode.CLASSIC
    assert client.calls == [("POST", "/api/v2/x", None, {"size": "1"})]


def test_requires_unified_switch_only_matches_40085() -> None:
    assert requires_unified_switch("40085") is True
    assert requires_unified_switch("40099") is False
    assert requires_unified_switch("40012") is False
    assert requires_unified_switch(None) is False


def test_unified_account_required_code_classified_as_auth_not_unknown() -> None:
    """DoD — 40085는 UNKNOWN_RESPONSE(fail-closed 기본값)가 아니라 명시적
    kind로 분류돼야 한다. 재시도해도 계정 모드는 안 바뀌므로 non-retryable."""
    kind = classify_body_code("40085")
    assert kind == ExchangeErrorKind.AUTH
    assert kind != ExchangeErrorKind.UNKNOWN_RESPONSE
    assert is_retryable(kind) is False


def test_wrong_environment_code_classified_as_auth_not_unknown() -> None:
    """DoD — 40099(데모 헤더를 보냈지만 데모 키가 아님)도 마찬가지."""
    kind = classify_body_code("40099")
    assert kind == ExchangeErrorKind.AUTH
    assert kind != ExchangeErrorKind.UNKNOWN_RESPONSE
    assert is_retryable(kind) is False
