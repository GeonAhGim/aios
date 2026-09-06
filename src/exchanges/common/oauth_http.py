"""BR-10(ADR-2026-09-06-I D6) — OAuth2 캐시 브로커 어댑터(KIS/NH) 공통 HTTP 내구성 로직.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-D, §10 BR (신설)

KIS(`_KISHTTPClient`, `src/exchanges/kis/adapter.py`)와 NH(`_NHHTTPClient`,
`src/exchanges/nh/adapter.py`)는 둘 다 "OAuth2 access token을 monotonic
시각 기준으로 캐싱하고, `httpx.TransportError`를 `RetryableExchangeError`로
변환한다"는 동일한 책임을 각자 손으로 다시 구현했다 — 이 모듈로 올려
NH가 먼저 소비한다(이 리프의 파일 스콥은 `src/exchanges/nh/`와
`src/exchanges/common/`뿐이라 KIS 이관은 후속 리프 몫).

토큰 응답 필드명·성공/실패 판정 규칙(KIS `rt_cd` vs NH `rsp_cd`/`rsp_msg`)은
거래소마다 달라 여기 넣지 않는다 — 이 모듈은 "캐싱 정책"과 "전송 오류
변환"만 다룬다.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from src.core.exceptions import RetryableExchangeError


class MonotonicTokenCache:
    """OAuth2 access token을 `time.monotonic()` 기준으로 캐싱한다.

    만료 판단 정책(안전 마진 등)은 호출부가 `set()`에 넘기는 `ttl_seconds`에
    이미 반영돼 있어야 한다 — 이 클래스는 값을 그대로 저장·비교만 한다.
    KIS의 "고정 23시간"과 NH의 "expires_in - 60초" 정책 차이를 이 클래스가
    흡수하지 않고 호출부 책임으로 남겨, 거래소별 정책 차이가 숨겨지지
    않게 한다.
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get(self) -> str | None:
        if self._token is not None and time.monotonic() < self._expires_at:
            return self._token
        return None

    def set(self, token: str, ttl_seconds: float) -> None:
        self._token = token
        self._expires_at = time.monotonic() + max(ttl_seconds, 0.0)


async def send_or_raise_retryable(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    venue: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str],
) -> httpx.Response:
    """`httpx.TransportError`(연결 실패 등)를 `RetryableExchangeError`로
    변환해 전송한다. 이 시점 이후의 응답 바디 해석(성공/실패 코드, JSON
    파싱)은 거래소마다 달라 호출부 책임으로 남긴다."""
    try:
        return await client.request(method, path, params=params, json=body, headers=headers)
    except httpx.TransportError as exc:
        raise RetryableExchangeError(f"{venue} 요청 전송 실패: {exc}") from exc
