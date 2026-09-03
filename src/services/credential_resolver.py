"""12.4 — 자격증명 조회 및 Adapter 인증 연동 (FD-3.1 개정).

Spec: 기능설계문서_v1.20.md#FD-12.2("FD-3.1 개정" 섹션), 02_exchange_adapter_v1.2.md

FD-3.1(인증)이 "시스템 전역 1세트"가 아니라 "이 요청의 user_id에 연결된
자격증명"을 쓰도록 연동한다. 매 요청 Adapter를 새로 만들지 않고 짧은
TTL로 캐싱한다(FD-12.2 원문 Draft). TTL은 time.monotonic() 기반 실제
경과시간으로 판정(이 세션에서 반복 적용한 원칙 — 호출 빈도에 좌우되지
않음).

완료조건(FD-12.2): 서로 다른 두 사용자가 동시에 각자의 키로 조회해도
섞이지 않아야 한다 — 캐시 키를 (user_id, exchange) 튜플로 둬 사용자별로
완전히 분리한다.

PLT-33: `ExchangeCredentialService.get_decrypted`가 항상 `scope="PAPER"`
행만 조회하도록 이관됐으므로(§10-8) 이 리졸버는 변경 없이 계속 PAPER
자격증명만 해석한다 — LIVE 행이 DB에 있어도 이 경로로는 절대 노출되지
않는다(`tests/integration/exchange/test_secret_scope_isolation.py`).
"""
from __future__ import annotations

import time
from uuid import UUID

from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.factory import build_adapter
from src.services.exchange_credential_service import AdapterFactory, ExchangeCredentialService

DEFAULT_TTL_SECONDS = 300.0  # Draft — 5분


class CredentialNotFoundError(Exception):
    """자격증명 미등록 또는 해지됨(FD-12.2 예외상황) — 이 계층까지 요청이
    오면 UI가 이미 해당 기능을 비활성화했어야 하는 시스템 오류로 취급."""


class CredentialResolver:
    def __init__(
        self,
        credential_service: ExchangeCredentialService,
        *,
        adapter_factory: AdapterFactory = build_adapter,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        demo_mode: bool = True,
    ) -> None:
        self._credential_service = credential_service
        self._adapter_factory = adapter_factory
        self._ttl_seconds = ttl_seconds
        self._demo_mode = demo_mode
        self._cache: dict[tuple[UUID, str], tuple[ExchangeAdapter, float]] = {}

    async def get_adapter(self, user_id: UUID, exchange: str) -> ExchangeAdapter:
        key = (user_id, exchange)
        cached = self._cache.get(key)
        if cached is not None:
            adapter, expires_at = cached
            if time.monotonic() < expires_at:
                return adapter

        decrypted = await self._credential_service.get_decrypted(user_id, exchange)
        if decrypted is None:
            raise CredentialNotFoundError(f"{exchange} 자격증명이 없거나 해지되었습니다.")
        api_key, api_secret, extra = decrypted

        adapter = self._adapter_factory(
            exchange, api_key, api_secret, extra, demo_mode=self._demo_mode
        )
        self._cache[key] = (adapter, time.monotonic() + self._ttl_seconds)
        return adapter

    def invalidate(self, user_id: UUID, exchange: str) -> None:
        """자격증명이 재등록/해지된 직후 호출 — 해지 API 라우터가 앱 조립
        단계(16번)에서 이 인스턴스를 통해 함께 호출해야 캐시가 TTL 동안
        예전 키를 계속 쓰는 것을 막을 수 있다."""
        self._cache.pop((user_id, exchange), None)
