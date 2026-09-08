"""L4_risk_and_safety_v1.0.md#§2 146행, §9 R-54 — 야간 결정 재생 CLI.

python -m src.tools.risk_replay --decision-id <uuid> | --since <ISO8601>

`replay_decision.py`(R-54)로 저장된 결정을 재계산해 WORM 원장과 대조한다.
exit 0=전부 일치, 2=불일치 1건 이상(재계산 불일치 및 번들 소실 포함),
1=도구 자체 예외(DB 접속 실패 등).
`--since`는 task-2060 FA-15 `scripts/replay_verify.py`와 같은 고정
윈도 관례(local_ci.py 매 커밋, §6 "재생 불일치 | 야간 replay" 배선).

`BundleNotFoundError`(저장된 rule_hash에 매칭되는 번들이 없음)는 조용히
건너뛰지 않는다 — 다른 결정과 나란히 실패로 세어 exit 2를 내되, 배치 전체를
죽이지 않고 나머지 decision_id를 계속 재생한다. 감사 로그에서 재현 불가
결정이 그냥 사라지면 안 되기 때문이다(task-2174).
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
