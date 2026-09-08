"""공용 원장 잔액 시드 헬퍼 — 잔액은 항상 분개(journal)를 거친다.

FA-15a(esc-2115, `docs/design/ADR-2026-09-08-A-governance-ratification-and-ops-decisions.md`
D4): 테스트 픽스처가 `ledger_balance`에 raw INSERT로 잔액을 직접 심으면,
그 계정이 나중에 실제 사건(구매·환불·정산)으로 다시 건드려질 때
`scripts/replay_verify.py`(순수 이벤트 재생, sequence 1부터 그 계정의
분개만 접는다 — raw seed floor를 알 방법이 없다)가 재생 잔액과 실제
잔액의 불일치를 낸다 — 실제로 esc-2115가 재현한 것이 이 경로다.

`post_topup`(LC-12b, `application/topup.py`)을 그대로 재사용해
`TOPUP_CONFIRMED` 분개로 시드한다 — `user_wallets`(레거시 투영)까지 같은
호출 안에서 함께 맞춰지므로, 이 헬퍼를 쓰는 테스트는 실제 충전 진입점과
동일한 경로를 탄다(raw SQL로 두 테이블을 따로 맞추던 예전 방식과 달리
드리프트가 애초에 생기지 않는다)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.topup import post_topup


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def seed_user_available_balance(
    pool: asyncpg.Pool,
    user_id: UUID,
    amount: Decimal,
    *,
    currency: Currency = Currency.KRW,
) -> None:
    """`USER:{user_id}:AVAILABLE` 잔액과 `user_wallets` 투영을 `amount`로
    맞춘다 — raw INSERT 없이 "이미 잔액이 있는 사용자"를 준비한다.
    `amount == 0`이면 아무 것도 하지 않는다(포스팅 라인 금액은 항상 > 0이어야
    하므로 §3.3 — 그리고 새 계정의 잔액은 이미 0이라 시드가 필요 없다)."""
    if amount == 0:
        return
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    topup_id = uuid4().int % 2_147_483_647
    async with pool.acquire() as conn, conn.transaction():
        await post_topup(
            conn,
            topup_id,
            user_id,
            amount,
            user_id,  # admin_id -- ledger_journal_entry.posted_by는 users FK다, user_id는
            # 이미 호출자가 만들어 뒀으니 그대로 재사용한다(셀프 시드일 뿐 실제 승인 아님).
            journal=journal,
            balances=balances,
            audit=audit,
            clock=_clock,
            currency=currency,
        )
