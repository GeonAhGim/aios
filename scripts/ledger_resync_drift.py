"""FA-15 follow-up -- one-time CLI for `application/resync_drift.py`.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15,
esc-ci-replay_verify (task-5749). See that module's docstring for why this
recomputes `ledger_balance` from the journal fold instead of posting another
correcting entry.

`--dry-run` (default) always rolls back at the end, so it is safe to run
repeatedly to inspect drift before committing -- same convention as
`scripts/ledger_backfill.py`. `--apply` commits.

Usage:
    python scripts/ledger_resync_drift.py                      # dry-run, PLATFORM house accounts
    python scripts/ledger_resync_drift.py --account PLATFORM:CASH_CLEARING --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.resync_drift import resync_account_balance
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
)

_DEFAULT_ACCOUNTS = (PLATFORM_CASH_CLEARING, PLATFORM_COMMISSION_REVENUE)


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _run(*, accounts: list[str], apply: bool) -> int:
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    exit_code = 0
    try:
        async with pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            try:
                for code in accounts:
                    report = await resync_account_balance(
                        conn, code, journal=journal, balances=balances
                    )
                    if not report.resynced:
                        print(f"{code}: drift 없음 (balance={report.actual_balance_before}).")
                        continue
                    print(
                        f"{code}: drift 발견 -- "
                        f"actual(balance={report.actual_balance_before}, "
                        f"last_entry_seq={report.actual_last_entry_seq_before}) != "
                        f"replayed(balance={report.replayed_balance}, "
                        f"last_entry_seq={report.replayed_last_entry_seq})."
                    )
            except BaseException:
                await tx.rollback()
                raise
            if apply:
                await tx.commit()
                print("커밋됨.")
            else:
                await tx.rollback()
                print("dry-run(롤백됨) -- --apply로 커밋하세요.")
    finally:
        await pool.close()
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--account",
        action="append",
        dest="accounts",
        help="대상 account_code (반복 가능, 기본: PLATFORM:CASH_CLEARING/COMMISSION_REVENUE)",
    )
    parser.add_argument("--apply", action="store_true", help="실제 커밋(기본은 dry-run 후 롤백)")
    args = parser.parse_args()
    accounts = args.accounts if args.accounts else list(_DEFAULT_ACCOUNTS)
    return asyncio.run(_run(accounts=accounts, apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
