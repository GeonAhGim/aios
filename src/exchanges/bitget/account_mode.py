"""L4-31 — Bitget Classic(v2)/Unified(v3, UTA) 계정 모드 감지 + 요청 조립.

Spec: docs/design/02d_bitget_uta_v3_spec_v1.md (task-2514 실측 근거),
      docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-B/§9

2026-09-09 실키 실측(task-2514 spec) — 서명/패스프레이즈는 정상 인증됐으나
Classic v2 엔드포인트(`/api/v2/spot/account/info`)가 40085
"You are in Unified Account mode, and the Classic Account API is not
supported" 로 거부됐다. Bitget이 계정을 UTA(Unified Trading Account)로
전환 중이므로, 어댑터는 이 코드를 신호 삼아 이후 요청을 v3(Unified)
엔드포인트로 전환해야 한다(공식 업그레이드 가이드 —
https://www.bitget.com/api-doc/classic/uta-api-upgrade-guide, 2026-09-09
조사: "서명 메커니즘은 v2/v3 동일, 기존 v2 API 키가 그대로 v3를 지원").

40099 "exchange environment is incorrect"는 계정 모드와 무관하게, 데모
헤더(`paptrading: 1`)를 보냈지만 그 키가 데모 키가 아닐 때 반환된다(동일
실측, task note). 계정 모드 전환 신호는 아니지만 UNKNOWN_RESPONSE로 새면
원인(데모 키 아님)을 알 수 없으므로 error_codes.py에서 명시적으로
분류한다 — 이 모듈은 그 판단에 쓰는 코드 상수만 소유한다.

계정 모드는 어댑터 인스턴스 하나의 수명 동안 단조 전환한다(CLASSIC ->
UNIFIED). Bitget의 UTA 전환은 사람이 대시보드에서 수행하는 단방향
작업이라(공식 지원 문서, 2026-09-09 조사) 같은 API 키가 세션 중간에
UNIFIED에서 CLASSIC으로 되돌아갈 수 없다 — 되돌아간다면 그건 새 키를
발급한 것이므로 새 어댑터 인스턴스가 될 것이다.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.exchanges.common.error_taxonomy import ExchangeError

# 실측(2026-09-09, task-2514) 바디 코드. 값 자체의 재시도 가능 여부 분류는
# error_codes.classify_body_code()가 맡는다 — 이 모듈은 "계정 모드를
# UNIFIED로 전환해야 하는가"라는 판단에만 40085를 쓴다.
UNIFIED_ACCOUNT_REQUIRED_CODE = "40085"
WRONG_ENVIRONMENT_CODE = "40099"


class BitgetAccountMode(str, Enum):
    CLASSIC = "classic"
    UNIFIED = "unified"


def requires_unified_switch(venue_code: str | None) -> bool:
    """`venue_code`가 "Classic API가 UTA 계정에서 거부됨" 신호인지."""
    return venue_code == UNIFIED_ACCOUNT_REQUIRED_CODE


# (method, path, params, body) — 모드가 주어지면 그 모드에 맞는 요청을
# 조립해 반환한다. 재시도 시 모드가 바뀌면 이 함수를 다시 호출해 body/path를
# 그 모드에 맞게 새로 만든다(v2 "size" -> v3 "qty" 같은 파라미터 개명이
# 있어, 실패한 요청의 body를 그대로 재전송하면 v3에서 다시 거부된다).
RequestSpec = tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]
RequestBuilder = Callable[[BitgetAccountMode], RequestSpec]


class AccountModeAwareClient(Protocol):
    account_mode: BitgetAccountMode

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


async def account_aware_request(
    client: AccountModeAwareClient, build: RequestBuilder
) -> dict[str, Any]:
    """`client.account_mode`에 맞는 요청을 보내고, CLASSIC 상태에서 40085를
    받으면 이 계정이 실제로는 UTA임이 확정된 것이므로 `client.account_mode`를
    UNIFIED로 전환한 뒤 `build`를 다시 호출해 v3 모양으로 재조립한 요청을
    1회 재시도한다(§8.3 원칙 — 이미 알아낸 사실로 스스로 복구할 수 있는
    실패를 그대로 전파하지 않는다).

    place_order 같은 자금 이동 요청도 안전하다: 40085는 체결 엔진 도달
    전에 거부된다는 뜻이므로(실측 — 서명은 통과하지만 요청 자체가 그
    API 표면으로 라우팅되지 않음) 재시도가 중복 주문을 만들지 않는다.
    `client._request`는 `FatalExchangeError`만 이 조건으로 던진다
    (adapter.py `_BitgetHTTPClient._request` — retryable=False 승격 경로).
    """
    method, path, params, body = build(client.account_mode)
    try:
        return await client._request(method, path, params=params, body=body)
    except FatalExchangeError as exc:
        cause = exc.__cause__
        if (
            client.account_mode is BitgetAccountMode.CLASSIC
            and isinstance(cause, ExchangeError)
            and requires_unified_switch(cause.venue_code)
        ):
            client.account_mode = BitgetAccountMode.UNIFIED
            method, path, params, body = build(client.account_mode)
            return await client._request(method, path, params=params, body=body)
        raise
