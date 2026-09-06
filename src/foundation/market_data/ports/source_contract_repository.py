"""DC-27 — source_contract 저장 포트.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1. 향후 유료 ingest_source 어댑터(OPENDART/ECOS/KRX/EODHD 등, D6 "계약
전에는 등록하지 않는다")는 `source_id` 문자열만 알고 이 포트를 모른다 —
이 포트는 `application/authorize_source_access.py` 게이트가 호출 전에
읽는 저장 계약이다.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg

from src.foundation.market_data.domain.entitlement.source_contract import SourceContract

__all__ = ["SourceContractRepository"]


@runtime_checkable
class SourceContractRepository(Protocol):
    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        """`source_id`의 현재 계약 행. 등급 승격은 이 행의 UPDATE이지
        새 행 삽입이 아니다(D1) — 행이 없으면 `None`을 돌려주고 호출자가
        fail-closed로 거부한다."""
        ...
