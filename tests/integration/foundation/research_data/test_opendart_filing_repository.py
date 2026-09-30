"""RD-20 — `PostgresCorporateActionFilingRepository` / `PostgresUnprocessedFilingQueue`
통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
DoD: 정정 공시는 UPDATE가 아니라 새 행(실 DB 행 수로 증명) + 그 상태에서도
정정 전 시점 질의가 정정 전 값을 돌려줌(point-in-time) + 파싱 실패 공시는
미처리 큐 테이블에 실제로 남음.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.contracts.v1 import (
    CorporateAction,
    RegisterInstrumentCommand,
    Venue,
)
from src.foundation.market_data.domain.corporate_action.point_in_time import resolve_as_of
from src.foundation.research_data.adapters.opendart.postgres_filing_repository import (
    PostgresCorporateActionFilingRepository,
)
from src.foundation.research_data.adapters.opendart.postgres_unprocessed_queue import (
    PostgresUnprocessedFilingQueue,
)

_EX_DATE = date(2026, 4, 1)


def _krx_symbol() -> str:
    return f"{uuid.uuid4().int % 900000 + 100000:06d}"


async def _make_instrument(pool: asyncpg.AsyncioMCDSN) -> uuid.UUID:
    repo = PostgresReferenceRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        instrument = await repo.register(
            conn,
            RegisterInstrumentCommand(
                venue=Venue.KIS_KRX,
                venue_symbol=_krx_symbol(),
                asset_class=AssetClass.KR_EQUITY,
                tick_size=Decimal("1"),
                lot_size=Decimal("1"),
                listed_at=datetime.now(timezone.utc) - timedelta(days=365),
                actor_subject_id=uuid.uuid4(),
                trace_id=uuid.uuid4(),
            ),
        )
    return instrument.instrument_id


def _split(
    instrument_id: uuid.UUID, *, ratio: str, known_at: datetime, source_ref: str
) -> CorporateAction:
    return CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument_id,
        ex_date=_EX_DATE,
        ratio=Decimal(ratio),
        source_ref=source_ref,
        known_at=known_at,
    )


@pytest.fixture
def filing_repo() -> PostgresCorporateActionFilingRepository:
    return PostgresCorporateActionFilingRepository()


@pytest.fixture
def queue() -> PostgresUnprocessedFilingQueue:
    return PostgresUnprocessedFilingQueue()


async def test_correction_appends_new_row_and_pit_query_preserves_pre_correction_value(
    pool: asyncpg.AsyncioMCDSN, filing_repo: PostgresCorporateActionFilingRepository
) -> None:
    instrument_id = await _make_instrument(pool)
    original = _split(
        instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-original-{instrument_id}",
    )
    correction = _split(
        instrument_id,
        ratio="5",
        known_at=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-correction-{instrument_id}",
    )

    async with pool.acquire() as conn, conn.transaction():
        await filing_repo.append(conn, original)
        await filing_repo.append(conn, correction)

    row_count = await pool.fetchval(
        "SELECT count(*) FROM md_corporate_action_filing WHERE instrument_id = $1",
        instrument_id,
    )
    assert row_count == 2  # 정정이 UPDATE였다면 1이었을 것

    async with pool.acquire() as conn:
        history = await filing_repo.list_history(conn, instrument_id)

    before = resolve_as_of(history, datetime(2026, 3, 5, tzinfo=timezone.utc))
    after = resolve_as_of(history, datetime(2026, 3, 15, tzinfo=timezone.utc))
    assert [a.ratio for a in before] == [Decimal("10")]
    assert [a.ratio for a in after] == [Decimal("5")]


async def test_append_is_idempotent_on_source_ref(
    pool: asyncpg.AsyncioMCDSN, filing_repo: PostgresCorporateActionFilingRepository
) -> None:
    instrument_id = await _make_instrument(pool)
    action = _split(
        instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-replay-{instrument_id}",
    )

    async with pool.acquire() as conn, conn.transaction():
        first = await filing_repo.append(conn, action)
    async with pool.acquire() as conn, conn.transaction():
        second = await filing_repo.append(conn, action)

    assert first == second
    row_count = await pool.fetchval(
        "SELECT count(*) FROM md_corporate_action_filing WHERE source_ref = $1",
        action.source_ref,
    )
    assert row_count == 1


async def test_append_without_known_at_is_rejected(
    pool: asyncpg.AsyncioMCDSN, filing_repo: PostgresCorporateActionFilingRepository
) -> None:
    instrument_id = await _make_instrument(pool)
    legacy_action = CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument_id,
        ex_date=_EX_DATE,
        ratio=Decimal("2"),
        source_ref="no-known-at",
    )

    with pytest.raises(ValueError):
        async with pool.acquire() as conn, conn.transaction():
            await filing_repo.append(conn, legacy_action)


async def test_parse_failure_lands_in_unprocessed_queue_table(
    pool: asyncpg.AsyncioMCDSN, queue: PostgresUnprocessedFilingQueue
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await queue.enqueue(
            conn,
            source_id="OPENDART",
            raw_payload={"rcept_no": "bad-filing", "report_type": "SPLIT"},
            reason="split_ratio_before 없음",
        )

    row = await pool.fetchrow(
        "SELECT * FROM research_opendart_unprocessed_filing WHERE source_id = $1 "
        "ORDER BY id DESC LIMIT 1",
        "OPENDART",
    )
    assert row is not None
    assert row["resolved"] is False
    payload = json.loads(row["raw_payload"])
    assert payload["rcept_no"] == "bad-filing"


async def test_append_rejects_unknown_instrument_id(
    pool: asyncpg.AsyncioMCDSN, filing_repo: PostgresCorporateActionFilingRepository
) -> None:
    """`instrument_id`가 `md_instrument`에 없으면 FK 제약으로 거부돼야 한다."""
    action = _split(
        uuid.uuid4(),
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-unknown-instrument-{uuid.uuid4()}",
    )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn, conn.transaction():
            await filing_repo.append(conn, action)


async def test_append_rejects_non_positive_ratio(
    pool: asyncpg.AsyncioMCDSN, filing_repo: PostgresCorporateActionFilingRepository
) -> None:
    """`ratio <= 0`은 애플리케이션 검증을 우회해도 DB CHECK로 거부돼야 한다
    (`ck_...`: `ratio > 0`)."""
    instrument_id = await _make_instrument(pool)
    action = _split(
        instrument_id,
        ratio="0",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-zero-ratio-{instrument_id}",
    )

    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn, conn.transaction():
            await filing_repo.append(conn, action)


class _FlakyOnceConnection:
    """실 커넥션을 감싸 `fetchrow` 최초 1회만 장애를 주입하는 래퍼 —
    append()가 "이미 있는지" 확인하는 첫 fetchrow에서 터지므로 INSERT는
    아예 시도되지 않는다(부분 행 없음을 증명)."""

    def __init__(self, real_conn: asyncpg.Connection) -> None:
        self._real_conn = real_conn
        self._armed = True

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        if self._armed:
            self._armed = False
            raise asyncpg.PostgresConnectionError("simulated transient outage")
        return await self._real_conn.fetchrow(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real_conn, name)


async def test_append_failure_injection_leaves_no_partial_row_and_retry_succeeds(
    pool: asyncpg.AsyncioMCDSN, filing_repo: PostgresCorporateActionFilingRepository
) -> None:
    """append 도중 커넥션 장애(실패주입)가 나면 부분 행이 남지 않고,
    장애가 사라진 뒤 같은 source_ref로 재시도하면 정상 반영돼야 한다."""
    instrument_id = await _make_instrument(pool)
    action = _split(
        instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-retry-{instrument_id}",
    )

    async with pool.acquire() as real_conn, real_conn.transaction():
        flaky = _FlakyOnceConnection(real_conn)
        with pytest.raises(asyncpg.PostgresConnectionError):
            await filing_repo.append(flaky, action)

    row_count_after_failure = await pool.fetchval(
        "SELECT count(*) FROM md_corporate_action_filing WHERE source_ref = $1",
        action.source_ref,
    )
    assert row_count_after_failure == 0

    async with pool.acquire() as conn, conn.transaction():
        result = await filing_repo.append(conn, action)

    assert result.ratio == Decimal("10")
    row_count_after_retry = await pool.fetchval(
        "SELECT count(*) FROM md_corporate_action_filing WHERE source_ref = $1",
        action.source_ref,
    )
    assert row_count_after_retry == 1
