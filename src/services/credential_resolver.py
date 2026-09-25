"""12.4 — Credential lookup and Adapter authentication integration (FD-3.1 revision).

Spec: 기능설계문서_v1.20.md#FD-12.2 ("FD-3.1 revision" section), 02_exchange_adapter_v1.2.md

Wire FD-3.1 (auth) to use "credentials linked to the user_id of this request"
rather than "one system-wide set". Cache with a short TTL instead of creating
a new Adapter per request (FD-12.2 original draft). TTL is evaluated using
actual elapsed time via time.monotonic() (a principle repeatedly applied in
this session — independent of call frequency).

Completion condition (FD-12.2): Two different users querying simultaneously
with their own keys must not be mixed — separate the cache key as a
(user_id, exchange) tuple so each user is fully isolated.

PLT-33: Since `ExchangeCredentialService.get_decrypted` has been migrated
to always query only `scope="PAPER"` rows (§10-8), this resolver continues
to interpret only PAPER credentials without modification — LIVE rows in the
DB are never exposed through this path
(`tests/integration/exchange/test_secret_scope_isolation.py`).
"""
from __future__ import annotations

import time
from uuid import UUID

from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.factory import build_adapter
from src.services.exchange_credential_service import AdapterFactory, ExchangeCredentialService

DEFAULT_TTL_SECONDS = 300.0  # Draft — 5분


class CredentialNotFoundError(Exception):
    """Credential not registered or revoked (FD-12.2 exception) — if a request
    reaches this layer, treat it as a system error where the UI should have
    already disabled the feature."""


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
        """Call immediately after credential re-registration or revocation —
        the revocation API router must invoke this instance during app assembly
        (step 16) to prevent the cache from continuing to use stale keys during
        the TTL window."""
        self._cache.pop((user_id, exchange), None)
