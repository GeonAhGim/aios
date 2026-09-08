"""L4_risk_and_safety_v1.0.md#§2 line 146, §9 R-54 — nightly decision-replay CLI.

python -m src.tools.risk_replay --decision-id <uuid> | --since <ISO8601>

Recomputes decisions stored via `replay_decision.py` (R-54) and compares them
against the WORM ledger. exit 0 = everything matches, 2 = one or more
mismatches (including recompute mismatches and missing bundles),
1 = the tool itself raised an exception (e.g. DB connection failure).
`--since` follows the same fixed-window convention as task-2060 FA-15's
`scripts/replay_verify.py` (local_ci.py on every commit, §6 "replay mismatch
| nightly replay" wiring).

`BundleNotFoundError` (no bundle matches the stored rule_hash) is not
silently skipped — it is counted as a failure alongside other decisions,
producing exit 2, without killing the whole batch; the remaining
decision_ids continue to be replayed. An irreproducible decision must not
simply vanish from the audit log (task-2174).

`DecisionCorruptError` (a stored row that fails the `RiskDecision` contract,
e.g. NULL `latency_us`) is handled the same way — task-2395, CI 77871f678ce2
observed a `pydantic.ValidationError` leaking through instead and killing the
whole batch.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.foundation.risk_gate.adapters.postgres_bundle_repository import PostgresBundleRepository
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    DecisionCorruptError,
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.application.replay_decision import BundleNotFoundError, replay


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def _run(*, decision_id: UUID | None, since: datetime | None) -> int:
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    try:
        decision_repo = PostgresDecisionRepository(pool)
        bundle_repo = PostgresBundleRepository(pool)
        if decision_id is not None:
            ids = [decision_id]
        else:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT decision_id FROM risk_decision WHERE evaluated_at >= $1 "
                    "ORDER BY evaluated_at",
                    since,
                )
            ids = [row["decision_id"] for row in rows]

        mismatches: list[UUID] = []
        for current_id in ids:
            try:
                result = await replay(decision_repo, bundle_repo, decision_id=current_id)
            except BundleNotFoundError as exc:
                print(
                    f"risk_replay: BUNDLE_NOT_FOUND {current_id} rule_hash={exc}",
                    file=sys.stderr,
                )
                mismatches.append(current_id)
                continue
            except DecisionCorruptError as exc:
                # task-2395 — a row that fails the RiskDecision contract (e.g.
                # NULL latency_us) is reported as unreproducible, not silently
                # skipped, and does not abort the rest of the batch.
                print(f"risk_replay: DECISION_CORRUPT {current_id} detail={exc}", file=sys.stderr)
                mismatches.append(current_id)
                continue
            if result.match:
                print(f"risk_replay: MATCH {current_id}")
            else:
                print(f"risk_replay: MISMATCH {current_id} diff={result.diff}", file=sys.stderr)
                mismatches.append(current_id)
    finally:
        await pool.close()

    if mismatches:
        print(f"risk_replay: FAIL ({len(mismatches)} mismatch)", file=sys.stderr)
        return 2
    print(f"risk_replay: OK ({len(ids)} checked)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--decision-id", type=str, default=None)
    group.add_argument("--since", type=str, default=None)
    args = parser.parse_args()
    decision_id = UUID(args.decision_id) if args.decision_id else None
    since = datetime.fromisoformat(args.since) if args.since else None
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return asyncio.run(_run(decision_id=decision_id, since=since))


if __name__ == "__main__":
    raise SystemExit(main())
