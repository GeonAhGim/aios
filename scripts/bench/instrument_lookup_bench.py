"""instrument_id 해석 벤치 — ADR-2026-09-09-C Decision 1 "instrument_id 해석 p95 200ms" 예산.

`resolve_instrument`(LA-24, `src/foundation/market_data/application/read_api.py`)를
반복 호출해 (venue, symbol) → `InstrumentRef`, instrument_id(UUID) → `InstrumentRef`
두 경로의 p50/p95/max(ms)를 재고 `docs/perf/instrument_lookup_bench.json`에 쓴다.

어댑터: `PostgresReferenceRepository`/`PostgresReferenceReader`(LA-12/LA-24 asyncpg
구현) — instrument_id 해석은 `md_instrument`/`md_symbol_alias` 조회를 실제로 거치므로
인메모리 스텁이 아니라 `TEST_DATABASE_URL`이 가리키는 실DB로 측정한다(명세 요구:
"테스트 DB 또는 인메모리 어댑터 명시"). 벤치용으로 등록하는 인스트루먼트는 전용
트랜잭션 안에서만 살아있고 끝에 롤백되어 DB에 흔적을 남기지 않는다.

사용: `TEST_DATABASE_URL=... python scripts/bench/instrument_lookup_bench.py
[--out PATH] [--iterations N]`.
`TEST_DATABASE_URL`이 없으면 measured=0으로 종료하지 않고 명시적으로 실패(exit 1) —
"DB 없이 조용히 스킵"은 이 벤치의 목적(실DB 경로 측정)과 모순되기 때문이다.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg  # noqa: E402

from scripts.bench._common import ROOT, summarize, write_result  # noqa: E402
from src.data.models.base import AssetClass  # noqa: E402
from src.foundation.market_data.adapters.postgres_reference_reader import (  # noqa: E402
    PostgresReferenceReader,
)
from src.foundation.market_data.adapters.postgres_reference_repository import (  # noqa: E402
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.read_api import resolve_instrument  # noqa: E402
from src.foundation.market_data.contracts.v1 import (  # noqa: E402
    RegisterInstrumentCommand,
    Venue,
)

BUDGET_MS = 200.0
DEFAULT_OUT = ROOT / "docs" / "perf" / "instrument_lookup_bench.json"
_VENUE_SYMBOL = "BENCHLKPUSDT"


def _dsn() -> str | None:
    raw = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw:
        return None
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def _measure(iterations: int) -> list[float]:
    dsn = _dsn()
    if dsn is None:
        raise RuntimeError(
            "TEST_DATABASE_URL이 필요합니다 — 실DB 경로를 측정하는 벤치라 스킵할 수 없다."
        )

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    if pool is None:
        raise RuntimeError("asyncpg.create_pool이 None을 반환했다 — DSN을 확인하라.")
    samples: list[float] = []
    try:
        conn = await pool.acquire()
        try:
            tx = conn.transaction()
            await tx.start()
            try:
                refs = PostgresReferenceRepository(pool=pool)
                reader = PostgresReferenceReader(pool=pool)
                now = datetime.now(timezone.utc)
                inst = await refs.register(
                    conn,
                    RegisterInstrumentCommand(
                        venue=Venue.BITGET,
                        venue_symbol=_VENUE_SYMBOL,
                        asset_class=AssetClass.CRYPTO,
                        tick_size=Decimal("0.01"),
                        lot_size=Decimal("0.001"),
                        listed_at=now,
                        actor_subject_id=uuid4(),
                        trace_id=uuid4(),
                    ),
                )

                for i in range(iterations):
                    by_symbol = i % 2 == 0
                    started = time.perf_counter()
                    await resolve_instrument(
                        conn,
                        refs=refs,
                        reader=reader,
                        venue=Venue.BITGET if by_symbol else None,
                        symbol=inst.venue_symbol if by_symbol else None,
                        instrument_id=None if by_symbol else inst.instrument_id,
                        now=now,
                    )
                    samples.append((time.perf_counter() - started) * 1000)
            finally:
                await tx.rollback()
        finally:
            await pool.release(conn)
    finally:
        await pool.close()
    return samples


def run(iterations: int) -> list[float]:
    return asyncio.run(_measure(iterations))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--iterations", type=int, default=50)
    args = parser.parse_args(argv)

    samples = run(args.iterations)
    result = summarize(
        "instrument_lookup_bench",
        samples,
        BUDGET_MS,
        note="resolve_instrument, TEST_DATABASE_URL 실DB(PostgresReferenceRepository/Reader), "
        "symbol/instrument_id 경로 번갈아 측정, 트랜잭션 롤백으로 무흔적",
    )
    write_result(result, args.out)

    status = "PASS" if result.passed else "FAIL"
    print(
        f"[instrument_lookup_bench] {status} p50={result.p50_ms:.2f}ms p95={result.p95_ms:.2f}ms "
        f"max={result.max_ms:.2f}ms budget<{BUDGET_MS:.0f}ms n={result.samples} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
