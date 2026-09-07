"""RD-20 — 기업행위 공시 저장 포트(append-only).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.

`append`는 절대 UPDATE하지 않는다 — 정정 공시는 같은 `(instrument_id,
action_type, ex_date)`라도 `source_ref`(DART 접수번호)가 다른 새 행으로
들어간다. `source_ref`는 저장소의 멱등키다: 같은 공시(같은 접수번호)를
두 번 넣으면 새 행을 만들지 않고 기존 행을 그대로 반환한다.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.market_data.contracts.v1 import CorporateAction

__all__ = ["CorporateActionFilingRepository"]


@runtime_checkable
class CorporateActionFilingRepository(Protocol):
    async def append(
        self, conn: asyncpg.Connection, action: CorporateAction
    ) -> CorporateAction:
        """`source_ref` 멱등 — 이미 있으면 기존 값을 그대로 반환(새 행을
        만들지 않음). `source_ref`가 다르면(정정 공시) 반드시 새 행이다."""
        ...

    async def list_history(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> list[CorporateAction]:
        """`known_at` 오름차순 전체 이력 — 정정으로 쌓인 모든 행을 포함한다.
        point-in-time 해석은 `domain/corporate_action/point_in_time.py`가
        한다(이 포트는 저장된 사실을 그대로 돌려줄 뿐이다)."""
        ...
