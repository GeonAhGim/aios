"""Clean up orphan test databases matching the aios_test_* pattern.

Identifies databases created by setup_test_db.py (prefix ``aios_test_``) that are
not currently in use as the active TEST_DATABASE_URL, and drops them to prevent
disk bloat from stale test environments.

Usage (repo root)::

    python -m scripts.cleanup_orphan_test_dbs          # dry-run (default)
    python -m scripts.cleanup_orphan_test_dbs --apply   # actually drop
"""
from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

import asyncpg

# Pattern used by setup_test_db.py to name worker-scoped test DBs.
_DB_PREFIX = "aios_test_"


def _test_db_url() -> str:
    """Return the active TEST_DATABASE_URL as a sync psycopg-compatible string."""
    url = os.environ["TEST_DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _active_db_name(url: str) -> str:
    """Extract the database name from a PostgreSQL URL."""
    # postgresql+asyncpg://user:pass@host:port/dbname
    after_at = url.split("@")[-1]
    path = after_at.split("/")[-1] if "/" in after_at else after_at.split(":")[-1]
    return path.split(":")[0].strip()


def _is_test_db(name: str) -> bool:
    return name.startswith(_DB_PREFIX) and name not in (_DB_PREFIX,)


async def list_orphan_dbs(active_db: str) -> list[str]:
    """Return names of ``aios_test_*`` databases that are not *active_db*."""
    dsn = _test_db_url().replace(f"/{active_db}", "")
    conn = await asyncpg.connect(dsn, statement_cache=None)
    try:
        rows = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE $1 AND datname <> $2",
            f"{_DB_PREFIX}%",
            active_db,
        )
        return [r["datname"] for r in rows]
    finally:
        await conn.close()


async def drop_db(name: str) -> None:
    """Drop a single database by reconnecting to *template1*."""
    dsn = _test_db_url().replace(
        f"/{_active_db_name(_test_db_url())}",
        "/template1",
    )
    conn = await asyncpg.connect(dsn, statement_cache=None)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> dict[str, Any]:
    """Run orphan-db cleanup. Returns ``{"orphan_dbs_cleaned": N, "errors": [...]}``."""
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually drop orphan databases (default: dry-run)"
    )
    args = parser.parse_args(argv)

    active_db = _active_db_name(_test_db_url())
    orphans = asyncio.run(list_orphan_dbs(active_db))
    errors: list[str] = []

    if not orphans:
        return {"orphan_dbs_cleaned": 0, "errors": []}

    if args.apply:
        for db in orphans:
            try:
                asyncio.run(drop_db(db))
            except asyncpg.PostgresError as exc:  # noqa: BLE001  # ratchet-allow: drop_db may fail with non-Postgres errors (e.g. connection)
                errors.append(f"failed to drop {db}: {exc}")
        return {"orphan_dbs_cleaned": len(orphans) - len(errors), "errors": errors}
    # dry-run (default): just report
    print(f"dry-run: {len(orphans)} orphan(s) found: {', '.join(orphans)}")
    return {"orphan_dbs_cleaned": 0, "errors": errors}


if __name__ == "__main__":
    raise SystemExit(main())
