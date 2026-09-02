"""LC-11 — `wallet_transactions`(+`user_wallets`) → 원장 소급 적재 CLI.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-11.

실제 사건 → 분개 매핑·검증은 전부
`src/foundation/ledger/application/backfill.py`(`backfill_ledger`)에 있다.
이 스크립트는 그 함수의 I/O 어댑터(입력 로딩 + 트랜잭션 경계)일 뿐이다 —
`--dry-run`(기본값)에서는 마지막에 항상 롤백하므로 여러 번 안전하게
재실행해 검증할 수 있다. `--apply`로만 실제 커밋한다.

사용:
    python scripts/ledger_backfill.py                # dry-run, 리포트만 출력
    python scripts/ledger_backfill.py --apply         # 실제 커밋

DATABASE_URL(`postgresql+asyncpg://...`)을 asyncpg DSN으로 바꿔 접속한다 —
`scripts/setup_test_db.py`와 같은 관례.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.backfill import (
    BackfillMismatchError,
    LegacyWalletTx,
    UnrecognizedTxGroupError,
    backfill_ledger,
)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _load_rows(conn: asyncpg.Connection) -> list[LegacyWalletTx]:
    rows = await conn.fetch(
        "SELECT id, user_id, tx_type, amount, related_purchase_id "
        "FROM wallet_transactions ORDER BY id"
    )
    return [LegacyWalletTx(**dict(row)) for row in rows]


async def _load_expected_balances(conn: asyncpg.Connection) -> dict[UUID, Decimal]:
    rows = await conn.fetch("SELECT user_id, balance FROM user_wallets")
    return {row["user_id"]: row["balance"] for row in rows}


async def _run(*, apply: bool) -> int:
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    try:
        journal = PostgresJournalRepository(pool)
        balances = PostgresBalanceRepository(pool)
        audit = PostgresAuditEventRepository(pool)

        async with pool.acquire() as conn:
            rows = await _load_rows(conn)
            expected = await _load_expected_balances(conn)
            print(f"wallet_transactions {len(rows)}건, user_wallets {len(expected)}건 로드.")

            tx = conn.transaction()
            await tx.start()
            try:
                report = await backfill_ledger(
                    conn, rows, expected,
                    journal=journal, balances=balances, audit=audit, clock=_clock,
                )
            except (BackfillMismatchError, UnrecognizedTxGroupError) as exc:
                await tx.rollback()
                print(f"백필 실패 — 롤백됨: {exc}", file=sys.stderr)
                return 1
            except BaseException:
                await tx.rollback()
                raise

            if apply:
                await tx.commit()
                print(
                    f"커밋됨: 분개 {report.entries_posted}건, "
                    f"잔액검증 {report.accounts_verified}건."
                )
            else:
                await tx.rollback()
                print(
                    f"dry-run(롤백됨): 분개 {report.entries_posted}건, "
                    f"잔액검증 {report.accounts_verified}건 — 문제 없음. --apply로 커밋하세요."
                )
        return 0
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="실제 커밋(기본은 dry-run 후 롤백)"
    )
    args = parser.parse_args()
    return asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
