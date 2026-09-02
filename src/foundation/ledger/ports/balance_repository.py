"""LC-8a — 원장 잔액 저장소 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §5, §9 LC-8.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/postgres_balance_repository.py,
LC-8b/task-320)은 모른다(71번 §4). `account_ids`/`account_id`는 항상
`account_code` 문자열(§3.3 `"USER:{uuid}:{UserSub}"` | `"PLATFORM:{NAME}"`)이다
— DB PK인 내부 `ledger_account.account_id` UUID가 아니다.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol, runtime_checkable

import asyncpg

from src.foundation.ledger.contracts.v1 import BalanceView


@runtime_checkable
class BalanceRepository(Protocol):
    async def get_for_update(
        self, conn: asyncpg.Connection, account_ids: Sequence[str]
    ) -> dict[str, BalanceView]:
        """§5 C: 분개에 관련된 `ledger_balance` 행을 `account_code` 오름차순으로
        정렬해 `FOR UPDATE`(동시 포스팅 간 교착 방지). 반환은
        `{account_code: BalanceView}`. 존재하지 않는 계정이 섞이면 구현체가
        예외를 던진다 — 원장은 미지 계정을 조용히 만들지 않는다(fail-closed)."""
        ...

    async def apply(
        self,
        conn: asyncpg.Connection,
        account_id: str,
        delta_balance: Decimal,
        delta_held: Decimal,
        expected_seq: int,
    ) -> BalanceView:
        """`conditional_update`(expected `last_entry_seq == expected_seq`)로
        `balance += delta_balance`, `held += delta_held`를 반영하고
        `last_entry_seq`를 새 분개의 `sequence_no`로 갱신한다. 기대와 실제가
        다르면 `ConcurrencyConflictError`(정상 경로에서는 발생하지 않는다 —
        이 호출 전에 `get_for_update`로 같은 트랜잭션 안에서 이미 행을 잠갔기
        때문이다). `available = balance - held ≥ 0`(RECEIVABLE 제외) 위반은
        DB CHECK가 막는다 — 이 메서드는 그 결과를 그대로 전파한다."""
        ...
