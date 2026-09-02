"""LC-8a — 원장 홀드 저장소 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §4.5, §9 LC-8.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/postgres_hold_repository.py,
LC-13/task 후속)은 모른다(71번 §4). 상태기계 자체(허용 전이)는
`domain/hold_state.py`(LC-5)의 책임이고, 이 포트는 그 결과를 저장만 한다.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import asyncpg

from src.foundation.ledger.contracts.v1 import HoldState, HoldView


class HoldRepository(Protocol):
    async def create(
        self,
        conn: asyncpg.Connection,
        *,
        hold_id: UUID,
        account_code: str,
        amount: Decimal,
        purpose: str,
        reference: str,
        expires_at: datetime,
        entry_id: UUID,
    ) -> HoldView:
        """§4.5 `place` 전이의 저장. 상태는 항상 `PENDING`으로 생성된다.
        `(purpose, reference)`가 이미 존재하면 구현체가 UNIQUE 위반을 던진다 —
        호출자가 사전에 멱등 여부를 판단해야 하며, 이 포트는 조용히 기존
        행으로 대체하지 않는다."""
        ...

    async def transition(
        self,
        conn: asyncpg.Connection,
        hold_id: UUID,
        *,
        expected_state: HoldState,
        new_state: HoldState,
        entry_id: UUID,
    ) -> HoldView:
        """§4.5 `capture`/`release`/`expire` 저장.
        `conditional_update`(expected `state == expected_state`)로 전이하고,
        이 전이를 일으킨 분개의 `entry_id`로 갱신한다. 기대 상태와 실제가
        다르면 `ConcurrencyConflictError` — `LEDGER_HOLD_STATE_INVALID` 코드
        매핑은 호출자 책임이다(이 포트는 도메인 에러 코드를 모른다)."""
        ...
