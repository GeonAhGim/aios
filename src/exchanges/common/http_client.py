"""6.6 — 믹스인이 `self`로 호출하는 서명 HTTP 계약을 구조적 타입으로 선언.

Spec: L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-40

거래소 믹스인(`*_mixin.py`)은 실제로는 어댑터 조립 시점(`BitgetAdapter` 등)에
`_{Bitget,Kis,Nh}HTTPClient`류의 공통 클래스와 다중상속으로만 합쳐진다.
믹스인 파일 하나만 정적으로 보면 `self._request`가 존재하지 않아
`# type: ignore[attr-defined]`로 억눌러 왔다 — 이 Protocol을 각 메서드의
`self` 타입으로 주석하면 mypy가 이 계약만으로 구조적 타입체크를 하고,
런타임 동작(실제로 어떤 클래스가 조립되는지)은 전혀 바뀌지 않는다.

편차: 스펙 표는 `_capabilities: ExchangeCapability` 속성을 언급하지만,
실제 어댑터들은 그 값을 속성이 아니라 `get_capabilities()` 메서드로 노출한다
(bitget/kis/nh 어댑터 전부 동일) — 존재하지 않는 속성을 Protocol에 넣으면
모든 구현체가 구조적으로 불일치해 오히려 새 mypy 오류를 만들므로, 실제
계약대로 메서드로 선언한다.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.exchanges.common.types import ExchangeCapability


@runtime_checkable
class SignedRequestClient(Protocol):
    """스팟 전용 거래소(bitget)가 요구하는 최소 HTTP 계약."""

    _demo_mode: bool

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def get_capabilities(self) -> ExchangeCapability: ...


@runtime_checkable
class KISHTTPClient(Protocol):
    """PLT-40b — KIS 믹스인이 요구하는 최소 HTTP 계약.

    `SignedRequestClient`를 상속하지 않고 별개로 선언한다: KIS `_request`는
    `tr_id`(거래ID)를 세 번째 위치 인자로 필수 요구해 bitget의
    `_request(method, path, *, params=, body=)`와 시그니처가 근본적으로
    다르다 — 상속해 오버라이드하면 mypy가 호환되지 않는 오버라이드로
    새 오류를 낸다. 계좌 필수 파라미터(CANO/ACNT_PRDT_CD)도 KIS 전용이다.
    """

    _cano: str
    _acnt_prdt_cd: str

    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
