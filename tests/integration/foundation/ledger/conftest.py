"""LC-8b 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로(테스트 전용 DB — 개발/운영 DB 접속 금지), 여기서는 그 값을
그대로 읽어 asyncpg DSN으로 변환하기만 한다.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import AccountType


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=64)
    yield p
    await p.close()


async def create_ledger_account(
    pool: asyncpg.Pool,
    *,
    account_type: AccountType = AccountType.ASSET,
    currency: Currency = Currency.KRW,
    allow_negative: bool = False,
    initial_balance: Decimal = Decimal("0"),
    initial_held: Decimal = Decimal("0"),
) -> str:
    """테스트 전용 고유 `account_code`로 `ledger_account`+`ledger_balance`
    행을 만든다 — LC-6 시드 계정(PLATFORM:*)을 공유하면 테스트 간 잔액
    상태가 서로 오염되므로 매 호출마다 새 계정을 쓴다."""
    account_code = f"PLATFORM:TEST_{uuid.uuid4().hex[:16].upper()}"
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, $4) RETURNING account_id",
            account_code,
            account_type.value,
            currency.value,
            allow_negative,
        )
        await conn.execute(
            "INSERT INTO ledger_balance "
            "(account_id, balance, held, allow_negative, last_entry_seq) "
            "VALUES ($1, $2, $3, $4, 0)",
            account_id,
            initial_balance,
            initial_held,
            allow_negative,
        )
    return account_code
