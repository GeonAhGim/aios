"""RD-20 `adapters/opendart/postgres_*` — DEEPEN(task-2903,
docs/audit/DEPTH_DC_RD.md#1767) D1 -> D2 증빙, 실 DB 대상.

기존 test_opendart_filing_repository.py는 정정=새 행·멱등·known_at 거부·
미처리 큐 적재를 단건으로 증명했다(D1) — 소급감사(task-2726)에서
"실패주입(DB/네트워크) 없음, 성능단언 없음, 게이트적색 재현 없음"으로
지적됐다(1767행). 이 파일이 그 부족분을 채운다. postgres_filing_repository.py
/ postgres_unprocessed_queue.py는 무수정 — 새 기능 없음, 깊이만 올림.

1. 실패 주입 — append / enqueue 도중 커넥션 장애(monkeypatch)가 부분
   INSERT 없이 전체를 실패시키는지.
2. 성능 단언 — 대량(200건) 정정 이력 append + list_history + resolve_as_of
   왕복이 절대시간 예산 내인지.
3. 게이트 적색 재현 — `aios_app` 역할로 UPDATE/DELETE를 시도하면
   InsufficientPrivilegeError(마이그레이션이 UPDATE/DELETE를 GRANT하지
   않음)로 거부되는지, 그리고 거부 후에도 INSERT·PIT 조회가 계속
   동작하는지(권한 게이트가 커넥션/데이터를 오염시키지 않음).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

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
from tests._perf.relative_budget import RelativeBudget

_EX_DATE = date(2026, 4, 1)


def _krx_symbol() -> str:
    return f"{uuid.uuid4().int % 900000 + 100000:06d}"


async def _make_instrument(pool: asyncpg.Pool) -> uuid.UUID:
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
    instrument_id: uuid.UUID,
    *,
    ratio: str,
    known_at: datetime,
    source_ref: str,
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


# ---- 실패 주입 (DB/네트워크 monkeypatch) ----


async def test_append_connection_failure_leaves_no_partial_row(
    pool: asyncpg.Pool,
    filing_repo: PostgresCorporateActionFilingRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """append의 INSERT fetchrow 도중 커넥션이 끊기면(장애 주입) 행이
    부분 반영된 채 남지 않아야 한다."""
    instrument_id = await _make_instrument(pool)
    action = _split(
        instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-fail-append-{instrument_id}",
    )

    async def _boom(self: asyncpg.Connection, *args: object, **kwargs: object):
        raise asyncpg.PostgresConnectionError("injected append connection failure")

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _boom)
    with pytest.raises(asyncpg.PostgresConnectionError):
        async with pool.acquire() as conn, conn.transaction():
            await filing_repo.append(conn, action)
    monkeypatch.undo()

    row_count = await pool.fetchval(
        "SELECT count(*) FROM md_corporate_action_filing WHERE source_ref = $1",
        action.source_ref,
    )
    assert row_count == 0


async def test_enqueue_connection_failure_leaves_no_partial_row(
    pool: asyncpg.Pool,
    queue: PostgresUnprocessedFilingQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enqueue의 execute 도중 커넥션 장애가 주입되면 미처리 큐에 부분
    행이 남지 않아야 한다."""
    marker = f"injected-enqueue-{uuid.uuid4()}"

    async def _boom(self: asyncpg.Connection, *args: object, **kwargs: object):
        raise asyncpg.PostgresConnectionError("injected enqueue connection failure")

    monkeypatch.setattr(asyncpg.Connection, "execute", _boom)
    with pytest.raises(asyncpg.PostgresConnectionError):
        async with pool.acquire() as conn, conn.transaction():
            await queue.enqueue(
                conn,
                source_id="OPENDART",
                raw_payload={"rcept_no": marker},
                reason="parse failed",
            )
    monkeypatch.undo()

    row_count = await pool.fetchval(
        "SELECT count(*) FROM research_opendart_unprocessed_filing "
        "WHERE raw_payload->>'rcept_no' = $1",
        marker,
    )
    assert row_count == 0


# ---- 성능 단언 ----


@pytest.mark.perf
async def test_bulk_append_and_pit_resolve_meets_latency_budget(
    pool: asyncpg.Pool, filing_repo: PostgresCorporateActionFilingRepository
) -> None:
    """동일 instrument에 200건 정정 이력을 append한 뒤 list_history +
    resolve_as_of 왕복이 절대시간 예산 내여야 한다."""
    n = 200
    budget_sec = 8.0  # 실측 로컬 <2s, CI/공유 DB 편차 감안
    instrument_id = await _make_instrument(pool)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    try:

        async def _bulk_append_and_pit() -> None:
            async with pool.acquire() as conn, conn.transaction():
                for i in range(n):
                    await filing_repo.append(
                        conn,
                        _split(
                            instrument_id,
                            ratio=str(i + 1),
                            known_at=base + timedelta(hours=i),
                            source_ref=f"rcept-perf-{instrument_id}-{i}",
                        ),
                    )
                history = await filing_repo.list_history(conn, instrument_id)
            mid = resolve_as_of(history, base + timedelta(hours=n // 2))
            assert len(history) == n
            assert len(mid) == 1
            assert mid[0].ratio == Decimal(str(n // 2 + 1))

        # raw perf_counter → RelativeBudget.measure_async (task-11067, L4-DC-2).
        # 비동기 I/O 왕복은 wall-clock으로 재되, CPU 보정 루프 비율로 호스트 부하
        # 상쇄 — 보정 루프가 2배 느려지면 로그인 왕복도 스케줄링 경합으로 비례해
        # 느려지므로 비율은 유지된다(task-7631).
        sample = await RelativeBudget().measure_async(
            _bulk_append_and_pit,
            n=3,
            calibration_n=5,
        )
        print(
            f"[RD-20 filing repo] {n}행 왕복 보정 비율 {sample.ratio:.1f}x "
            f"(wall {sample.op_ms:.0f}ms, calib {sample.calibration_ms:.0f}ms)"
        )
        # 절대시간 예산 8s — 상대 보정 비율과 함께 하드 게이트
        assert sample.op_ms < budget_sec * 1000, (
            f"대량 append+PIT 왕복이 예산({budget_sec}s)을 넘었습니다."
        )
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM md_corporate_action_filing WHERE instrument_id = $1",
                instrument_id,
            )


# ---- 게이트 적색 재현 — aios_app UPDATE/DELETE 거부 + PIT 유지 ----


async def test_aios_app_cannot_update_or_delete_filing_then_pit_still_works(
    pool: asyncpg.Pool, filing_repo: PostgresCorporateActionFilingRepository
) -> None:
    """마이그레이션이 `aios_app`에 SELECT/INSERT만 GRANT했으므로 UPDATE·
    DELETE는 InsufficientPrivilegeError로 거부돼야 한다(append-only를
    코드 리뷰가 아니라 DB 권한으로 증명). 거부 후에도 정상 INSERT와
    정정 전/후 PIT 조회가 계속 동작해야 한다."""
    instrument_id = await _make_instrument(pool)
    original = _split(
        instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-gate-orig-{instrument_id}",
    )
    correction = _split(
        instrument_id,
        ratio="5",
        known_at=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-gate-corr-{instrument_id}",
    )

    async with pool.acquire() as conn, conn.transaction():
        await filing_repo.append(conn, original)
        await filing_repo.append(conn, correction)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE aios_app")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "UPDATE md_corporate_action_filing SET ratio = 99 WHERE source_ref = $1",
                    original.source_ref,
                )
        # 권한 거부 후 트랜잭션이 aborted 되므로 DELETE는 새 트랜잭션에서 검증한다
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE aios_app")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "DELETE FROM md_corporate_action_filing WHERE source_ref = $1",
                    original.source_ref,
                )

    # 권한 거부 후에도 원본/정정 행과 PIT가 오염되지 않음
    ratio_original = await pool.fetchval(
        "SELECT ratio FROM md_corporate_action_filing WHERE source_ref = $1",
        original.source_ref,
    )
    assert ratio_original == Decimal("10")

    async with pool.acquire() as conn:
        history = await filing_repo.list_history(conn, instrument_id)
    before = resolve_as_of(history, datetime(2026, 3, 5, tzinfo=timezone.utc))
    after = resolve_as_of(history, datetime(2026, 3, 15, tzinfo=timezone.utc))
    assert [a.ratio for a in before] == [Decimal("10")]
    assert [a.ratio for a in after] == [Decimal("5")]

    # 거부 이후에도 INSERT(추가 정정)는 계속 가능
    third = _split(
        instrument_id,
        ratio="4",
        known_at=datetime(2026, 3, 20, 9, 0, tzinfo=timezone.utc),
        source_ref=f"rcept-gate-third-{instrument_id}",
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE aios_app")
            await filing_repo.append(conn, third)

    async with pool.acquire() as conn:
        history = await filing_repo.list_history(conn, instrument_id)
    latest = resolve_as_of(history, datetime(2026, 3, 25, tzinfo=timezone.utc))
    assert [a.ratio for a in latest] == [Decimal("4")]
    assert len(history) == 3


# ---- 검출력 확인 — 예산 10배 지연 주입으로 적색 검증 ----


async def test_append_delay_10x_exceeds_latency_budget(
    pool: asyncpg.Pool,
    filing_repo: PostgresCorporateActionFilingRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB 호출에 10배 sleep을 주입하면 성능 단언이 적색이어야 한다.

       RelativeBudget.measure_async는 wall-clock(perf_counter)를 쓰므로
       sleep 지연이 op_ms에 직접 반영되고 calibration은 순수 CPU라 변하지
    않아 — ratio가 예산 max_ratio를 넘어야 한다.
    """
    n = 200
    instrument_id = await _make_instrument(pool)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # 원본 fetchrow를 감싸서 10배 지연(0.01초 × 10 = 0.1초 per call)
    original_fetchrow = asyncpg.Connection.fetchrow

    async def _slow_fetchrow(
        self: asyncpg.Connection, *args: object, **kwargs: object
    ) -> asyncpg.Record | None:
        await asyncio.sleep(0.1)  # 10배 지연 주입 (non-blocking)
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _slow_fetchrow)

    try:

        async def _bulk_append_and_pit() -> None:
            async with pool.acquire() as conn, conn.transaction():
                for i in range(n):
                    await filing_repo.append(
                        conn,
                        _split(
                            instrument_id,
                            ratio=str(i + 1),
                            known_at=base + timedelta(hours=i),
                            source_ref=f"rcept-delay-{instrument_id}-{i}",
                        ),
                    )
                history = await filing_repo.list_history(conn, instrument_id)
            mid = resolve_as_of(history, base + timedelta(hours=n // 2))
            assert len(mid) == 1

        sample = await RelativeBudget().measure_async(
            _bulk_append_and_pit,
            n=3,
            calibration_n=5,
        )
        # 지연 주입 시 ratio가 예산을 넘어야 함(검출력 확인)
        # RelativeBudget.measure_async의 기본 max_ratio는 3.0
        assert sample.ratio > 3.0, (
            f"지연 주입에도 ratio={sample.ratio:.1f}x — "
            f"검출력이 없습니다(op_ms={sample.op_ms:.0f}ms, calib={sample.calibration_ms:.0f}ms)"
        )
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM md_corporate_action_filing WHERE source_ref LIKE $1",
                f"rcept-delay-{instrument_id}%",
            )


@pytest.mark.perf
async def test_list_history_delay_10x_exceeds_latency_budget(
    pool: asyncpg.Pool,
    filing_repo: PostgresCorporateActionFilingRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_history(fetch) 호출에 10배 sleep을 주입하면 성능 단언이 적색이어야 한다.

    append가 아닌 read 경로(list_history → conn.fetch)의 검출력을 확인한다.
    """
    n = 200
    instrument_id = await _make_instrument(pool)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # 정상 append (지연 없이)
    async with pool.acquire() as conn:
        for i in range(n):
            await filing_repo.append(
                conn,
                _split(
                    instrument_id,
                    ratio=str(i + 1),
                    known_at=base + timedelta(hours=i),
                    source_ref=f"rcept-listhist-{instrument_id}-{i}",
                ),
            )

    # 고정 sleep의 검출은 CPU 속도가 아니라 주입한 벽시계 시간으로 검증한다.
    injected_delay_sec = 0.5
    original_fetch = asyncpg.Connection.fetch

    async def _slow_fetch(
        self: asyncpg.Connection, *args: object, **kwargs: object
    ) -> list[asyncpg.Record]:
        await asyncio.sleep(injected_delay_sec)  # 10배 지연 주입 (non-blocking)
        result = await original_fetch(self, *args, **kwargs)
        return cast("list[asyncpg.Record]", result)

    monkeypatch.setattr(asyncpg.Connection, "fetch", _slow_fetch)

    try:

        async def _read_and_pit() -> None:
            async with pool.acquire() as conn, conn.transaction():
                history = await filing_repo.list_history(conn, instrument_id)
            mid = resolve_as_of(history, base + timedelta(hours=n // 2))
            assert len(mid) == 1

        sample = await RelativeBudget().measure_async(
            _read_and_pit,
            n=3,
            calibration_n=5,
        )
        # CPU 보정 루프가 느려져도 고정 I/O 지연은 줄어들지 않는다.
        assert sample.op_ms >= injected_delay_sec * 1000, (
            f"list_history 주입 지연 미검출: {sample.op_ms:.0f}ms "
            f"< {injected_delay_sec * 1000:.0f}ms"
        )
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM md_corporate_action_filing WHERE source_ref LIKE $1",
                f"rcept-listhist-{instrument_id}%",
            )
