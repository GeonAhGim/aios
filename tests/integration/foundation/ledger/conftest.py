"""LC-8b 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로(테스트 전용 DB — 개발/운영 DB 접속 금지), 여기서는 그 값을
그대로 읽어 asyncpg DSN으로 변환하기만 한다.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import pytest

from scripts import replay_verify
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


@pytest.fixture(autouse=True)
async def _ledger_control_clean_slate(pool):
    """`ledger_control`(id=1)의 `write_frozen`은 원장 전역 상태라, LC-10
    tamper 통합테스트(`test_verify_integrity.py`)가 세운 동결이 하나라도
    남으면 이 디렉터리의 다른 모든 리프(구매·환불·정산 `post_entry` 호출)가
    영구히 거부돼 재실행마다 false-red를 낸다(task-312 QA가 공유
    TEST_DATABASE_URL에서 실제로 재현). 테스트 바디 안의 `try/finally`만으로는
    "이전 실행이 크래시·타임아웃으로 중간에 죽어 finally를 못 밟은 경우"를
    못 막으므로, 매 테스트 전(이전 잔류 자기치유)·후(이번 실행의 잔류 예방)
    양쪽에서 pytest가 보장하는 fixture teardown으로 무조건 원복한다 —
    이미 원복돼 있으면 UPDATE가 그냥 no-op이라 다른 테스트에 부작용이 없다."""

    async def _reset() -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE ledger_control SET write_frozen = FALSE, frozen_reason = NULL, "
                "frozen_at = NULL WHERE id = 1"
            )

    await _reset()
    yield
    await _reset()


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
    상태가 서로 오염되므로 매 호출마다 새 계정을 쓴다.

    FA-15a(esc-2115, ADR-2026-09-08-A D4): `initial_balance`/`initial_held`가
    0이 아니어도 `post_entry` 분개로 시드하지 않는다 — 이 계정은
    `PLATFORM:TEST_*`라 `chart_of_accounts.account_type()`이 모르는 이름이라
    `post_entry`(MANUAL_ADJUSTMENT 포함)로 애초에 잔액을 올릴 방법이 없다.
    실제로 이 값을 0이 아니게 쓰는 유일한 호출자
    (`test_postgres_balance_repository.py`)는 `PostgresBalanceRepository`
    자신을 화이트박스로 시험할 뿐 `ledger_journal_entry`/`ledger_posting_line`
    행을 절대 만들지 않으므로, `scripts/replay_verify.py`의 "이 창에서
    실제로 건드려진 계정" 스캔(`ledger_posting_line` JOIN)에 이 계정이 잡힐
    일 자체가 없다 — 그래서 여기서만 raw 시드를 허용한다."""
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
        # audit-allow: ledger_balance_raw_seed -- 위 docstring 참고: 이 계정은
        # 절대 journal에 posting_line을 남기지 않아 replay_verify 대상 밖이다.
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


@pytest.fixture(scope="module")
async def _sentinel_watch_orders_residue(pool):
    """D3 test harness: watch for orphaned orders rows (no matching event).

    Sentinel hook runs after each module to detect if any test left an orders
    row without the corresponding order_event(s). This catches the residue class
    task-11356 partially fixed (orders row mutated without an event), catching
    the originating test instead of the victim test (test_resync_drift).

    Simple variant: count orders table mutations. A test that commits an orders
    row change without a matching event will be pinned down when replay_verify
    sees the mismatch.
    """
    orders_count_before = None

    async def _get_orders_count() -> int:
        """Count rows in orders table; covers the shared TEST_DATABASE_URL."""
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT count(*) FROM orders")
        return count or 0

    # Record baseline before module tests run
    orders_count_before = await _get_orders_count()

    yield

    # After all tests in module, check for drift
    orders_count_after = await _get_orders_count()
    as_of = datetime.now(timezone.utc)

    # Run replay_verify to catch any orders row ↔ event mismatch. The verify()
    # call itself is wrapped so infrastructure failures (network/schema) don't
    # mask the deliberate AssertionError raised below -- catching Exception
    # around both would swallow our own raise since AssertionError is-a Exception.
    try:
        verify_result = await replay_verify.verify(pool, as_of=as_of, hours=24)
    except Exception as e:  # noqa: BLE001 -- verify() infra failure (network/schema) must not block the module; logged for manual triage instead
        logging.getLogger(__name__).warning(
            "Sentinel watch aborted: replay_verify.verify() failed (%s)", e
        )
        return

    if not verify_result.ok:
        mismatches = [
            m
            for m in verify_result.mismatches
            if m.domain == "orders" or (hasattr(m, "key") and isinstance(m.key, str))
        ]
        if mismatches:
            msg = (
                f"Sentinel: orders residue detected after module teardown. "
                f"Table mutations: {orders_count_before} → {orders_count_after}. "
                f"Mismatches: {mismatches}"
            )
            raise AssertionError(msg)
