"""ingest_candles 스탠드얼론 테스트 — DB 없이 검증.

역할 분리: ingest_candles 애플리케이션 서비스( pool+deps ) vs
bitget_ingest_source HTTP 매핑 / batch_hash 결정론성.
CLAUDE.md §7 file policy — 책임 단위 분할.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from src.exchanges.bitget.adapter import BitgetAdapter
from src.foundation.market_data.adapters.bitget_ingest_source import BitgetIngestSource
from src.foundation.market_data.contracts.v1 import (
    Timeframe,
    Venue,
)


async def test_bitget_ingest_source_fetches_and_filters_via_mock_transport():
    """bitget_ingest_source — MockTransport로만 검증(실 HTTP 금지)."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    ts_ms = int(t0.timestamp() * 1000)
    rows = [
        [str(ts_ms), "100", "110", "90", "105", "10", "1000", "1000"],
        [str(ts_ms + 60_000), "105", "115", "95", "110", "12", "1200", "1200"],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/candles"
        assert request.url.params.get("symbol") == "BTCUSDT"
        return httpx.Response(200, json={"code": "00000", "msg": "success", "data": rows})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    adapter = BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)
    source = BitgetIngestSource(adapter)

    result = await source.fetch_candles(
        Venue.BITGET, "BTCUSDT", Timeframe.M1, t0, t0 + timedelta(minutes=1)
    )

    assert len(result) == 1
    assert result[0].open_time == t0
    assert result[0].close == Decimal("105")


def test_batch_hash_is_stable_across_different_pythonhashseed() -> None:
    """[health:audit_correction] task-10471 — AUDIT_2026-10-01_data_ingest_replay.md §2 F7(L).

    The audit claims `ingest_candles.py` builds `batch_hash` with the builtin
    `hash()`, which is `PYTHONHASHSEED`-randomized per process and would break
    dedup. The current call site (`ingest_candles.py` -> `batch_hash(rekeyed)`)
    actually delegates to `domain/lineage.py::batch_hash`, which is
    `hashlib.sha256`-based and not seed-dependent — this test proves the value
    `ingest_candles` persists as `md_ingest_batch.batch_hash` is identical
    across two processes with different `PYTHONHASHSEED`, i.e. the audited
    failure mode does not reproduce against the code as it stands.
    """
    script = (
        "from decimal import Decimal\n"
        "from datetime import datetime, timedelta, timezone\n"
        "import uuid\n"
        "from src.foundation.market_data.contracts.v1 import (\n"
        "    CandleRecord, SeriesKey, Timeframe, Venue,\n"
        ")\n"
        "from src.foundation.market_data.domain.lineage import batch_hash\n"
        "instrument_id = uuid.UUID('11111111-1111-1111-1111-111111111111')\n"
        "t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)\n"
        "records = [\n"
        "    CandleRecord(\n"
        "        key=SeriesKey(\n"
        "            venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1,\n"
        "        ),\n"
        "        open_time=t0 + timedelta(minutes=i),\n"
        "        close_time=t0 + timedelta(minutes=i + 1),\n"
        "        open=Decimal('100'), high=Decimal('110'), low=Decimal('90'),\n"
        "        close=Decimal('105'), volume=Decimal('10'),\n"
        "    )\n"
        "    for i in range(3)\n"
        "]\n"
        "print(batch_hash(records))\n"
    )

    def _run_with_seed(seed: str) -> str:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parents[4]),
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    digest_seed_0 = _run_with_seed("0")
    digest_seed_1 = _run_with_seed("1")
    digest_seed_random = _run_with_seed("random")

    assert digest_seed_0 == digest_seed_1 == digest_seed_random, (
        "batch_hash must be identical regardless of PYTHONHASHSEED — "
        f"got {digest_seed_0!r}, {digest_seed_1!r}, {digest_seed_random!r}"
    )
