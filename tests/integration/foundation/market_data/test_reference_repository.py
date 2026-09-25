"""PostgresReferenceRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-12.
DoD(task-451): "심볼 RENAME 별칭이 기간(valid_from/valid_to)으로 정확히
해석됨", negative 최소 1개("별칭 기간 중복 → EXCLUDE 위반").
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.adapters.postgres_reference_repository import (
    AliasPeriodOverlapError,
    CorporateActionDigestMismatchError,
    DuplicateInstrumentError,
    PostgresReferenceRepository,
)
from src.foundation.market_data.contracts.v1 import (
    CorporateAction,
    RegisterInstrumentCommand,
    SymbolStatus,
    Venue,
)


def _krx_symbol() -> str:
    return f"{uuid.uuid4().int % 900000 + 100000:06d}"


def _register_cmd(*, venue_symbol: str, listed_at: datetime) -> RegisterInstrumentCommand:
    return RegisterInstrumentCommand(
        venue=Venue.KIS_KRX,
        venue_symbol=venue_symbol,
        asset_class=AssetClass.KR_EQUITY,
        tick_size=Decimal("1"),
        lot_size=Decimal("1"),
        listed_at=listed_at,
        actor_subject_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )


@pytest.fixture
def repo(pool):
    return PostgresReferenceRepository(pool)


async def test_register_then_get_instrument_resolves_current_symbol(pool, repo):
    symbol = _krx_symbol()
    listed_at = datetime.now(timezone.utc) - timedelta(days=1)
    async with pool.acquire() as conn, conn.transaction():
        instrument = await repo.register(
            conn, _register_cmd(venue_symbol=symbol, listed_at=listed_at)
        )

    assert instrument.status == SymbolStatus.PENDING
    assert instrument.canonical_symbol == symbol

    async with pool.acquire() as conn, conn.transaction():
        found = await repo.get_instrument(conn, Venue.KIS_KRX, symbol, datetime.now(timezone.utc))

    assert found is not None
    assert found.instrument_id == instrument.instrument_id


async def test_get_instrument_returns_none_for_unknown_symbol(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        found = await repo.get_instrument(
            conn, Venue.KIS_KRX, _krx_symbol(), datetime.now(timezone.utc)
        )
    assert found is None


async def test_register_duplicate_venue_symbol_raises(pool, repo):
    symbol = _krx_symbol()
    listed_at = datetime.now(timezone.utc)
    async with pool.acquire() as conn, conn.transaction():
        await repo.register(conn, _register_cmd(venue_symbol=symbol, listed_at=listed_at))

    with pytest.raises(DuplicateInstrumentError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.register(conn, _register_cmd(venue_symbol=symbol, listed_at=listed_at))


async def test_rename_alias_resolves_only_within_its_own_period(pool, repo):
    """DoD 핵심: RENAME 별칭이 valid_from/valid_to 기간으로 정확히 해석된다."""
    old_symbol = _krx_symbol()
    new_symbol = _krx_symbol()
    listed_at = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        instrument = await repo.register(
            conn, _register_cmd(venue_symbol=old_symbol, listed_at=listed_at)
        )

    before_rename = listed_at + timedelta(days=1)
    async with pool.acquire() as conn, conn.transaction():
        via_old_before_rename = await repo.get_instrument(
            conn, Venue.KIS_KRX, old_symbol, before_rename
        )
    assert via_old_before_rename is not None
    assert via_old_before_rename.instrument_id == instrument.instrument_id

    async with pool.acquire() as conn, conn.transaction():
        await repo.add_alias(conn, instrument.instrument_id, Venue.KIS_KRX, new_symbol)

    after_rename = datetime.now(timezone.utc)
    async with pool.acquire() as conn, conn.transaction():
        via_old_after_rename = await repo.get_instrument(
            conn, Venue.KIS_KRX, old_symbol, after_rename
        )
    assert via_old_after_rename is None, "RENAME 후 옛 심볼은 더 이상 유효하지 않아야 한다"

    async with pool.acquire() as conn, conn.transaction():
        via_new_after_rename = await repo.get_instrument(
            conn, Venue.KIS_KRX, new_symbol, after_rename
        )
    assert via_new_after_rename is not None
    assert via_new_after_rename.instrument_id == instrument.instrument_id

    async with pool.acquire() as conn, conn.transaction():
        via_old_still_before_rename = await repo.get_instrument(
            conn, Venue.KIS_KRX, old_symbol, before_rename
        )
    assert via_old_still_before_rename is not None, "닫힌 기간 이전 조회는 여전히 유효해야 한다"


async def test_add_alias_overlapping_period_raises_exclusion_violation(pool, repo):
    """negative: 서로 다른 인스트루먼트가 같은 (venue, alias_symbol)에 겹치는
    유효기간을 주장하면 md_symbol_alias의 EXCLUDE 제약이 막아야 한다."""
    shared_symbol = _krx_symbol()
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn, conn.transaction():
        await repo.register(
            conn, _register_cmd(venue_symbol=shared_symbol, listed_at=now - timedelta(days=5))
        )
    other_symbol = _krx_symbol()
    async with pool.acquire() as conn, conn.transaction():
        instrument_b = await repo.register(
            conn, _register_cmd(venue_symbol=other_symbol, listed_at=now - timedelta(days=5))
        )

    with pytest.raises(AliasPeriodOverlapError):
        async with pool.acquire() as conn, conn.transaction():
            # instrument_a의 별칭(valid_to=NULL, 아직 열려 있음)과 겹치는 채로
            # instrument_b가 같은 심볼을 새 별칭으로 주장한다.
            await repo.add_alias(conn, instrument_b.instrument_id, Venue.KIS_KRX, shared_symbol)


async def test_record_action_is_idempotent_on_replay(pool, repo):
    symbol = _krx_symbol()
    listed_at = datetime.now(timezone.utc) - timedelta(days=30)
    async with pool.acquire() as conn, conn.transaction():
        instrument = await repo.register(
            conn, _register_cmd(venue_symbol=symbol, listed_at=listed_at)
        )

    action = CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument.instrument_id,
        ex_date=listed_at.date() + timedelta(days=1),
        ratio=Decimal("2"),
        source_ref=f"test:{uuid.uuid4().hex}",
    )

    async with pool.acquire() as conn, conn.transaction():
        first = await repo.record_action(conn, action)
    async with pool.acquire() as conn, conn.transaction():
        second = await repo.record_action(conn, action)

    assert second == first

    async with pool.acquire() as conn, conn.transaction():
        actions = await repo.list_actions(conn, instrument.instrument_id)
    assert len(actions) == 1


async def test_record_action_different_content_same_key_raises(pool, repo):
    symbol = _krx_symbol()
    listed_at = datetime.now(timezone.utc) - timedelta(days=30)
    async with pool.acquire() as conn, conn.transaction():
        instrument = await repo.register(
            conn, _register_cmd(venue_symbol=symbol, listed_at=listed_at)
        )

    ex_date = listed_at.date() + timedelta(days=1)
    source_ref = f"test:{uuid.uuid4().hex}"
    action = CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument.instrument_id,
        ex_date=ex_date,
        ratio=Decimal("2"),
        source_ref=source_ref,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.record_action(conn, action)

    conflicting = action.model_copy(update={"ratio": Decimal("3")})
    with pytest.raises(CorporateActionDigestMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.record_action(conn, conflicting)


# ---- DEEPEN(task-2967, docs/audit/DEPTH_LA_LB_LC.md#451) -----------------
# 실패 주입(monkeypatch) / 수치 성능 단언 / 게이트 적색 재현.


async def test_register_alias_insert_connection_failure_leaves_no_orphan_instrument(
    pool, repo, monkeypatch
):
    """실패 주입: register()는 md_instrument INSERT(fetchrow) 후 별칭
    INSERT(execute)를 별도로 낸다 — EXCLUDE 위반이 아니라 순수 드라이버
    장애(커넥션 단절 흉내)로 별칭 INSERT가 실패하면, 같은 트랜잭션 안에서
    instrument 행만 남고 별칭 없는 고아 레코드가 생기지 않아야 한다."""
    symbol = _krx_symbol()
    listed_at = datetime.now(timezone.utc)

    original_execute = asyncpg.Connection.execute

    async def _boom(self: asyncpg.Connection, query: str, *args: object, **kwargs: object) -> str:
        # BEGIN/COMMIT 등 트랜잭션 제어문까지 막으면 register() 이전에
        # 터져버려 의도한 실패 지점(별칭 INSERT)을 시험할 수 없다 — 그
        # 쿼리만 골라 장애를 흉내낸다.
        if "INSERT INTO md_symbol_alias" in query:
            raise asyncpg.PostgresConnectionError("injected connection failure")
        return await original_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", _boom)
    with pytest.raises(asyncpg.PostgresConnectionError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.register(conn, _register_cmd(venue_symbol=symbol, listed_at=listed_at))
    monkeypatch.undo()

    async with pool.acquire() as conn, conn.transaction():
        found = await repo.get_instrument(conn, Venue.KIS_KRX, symbol, datetime.now(timezone.utc))
    assert found is None, "롤백되지 않으면 별칭 없는 고아 instrument가 조회될 것이다"


@pytest.mark.perf
async def test_get_instrument_lookup_p95_under_budget(pool, repo):
    """수치 성능 단언: get_instrument()는 시점별 심볼 해석에 매번 불리는
    읽기 경로다(§9.2 LA-12) — 반복 조회의 p95 지연이 원장 append p95 예산
    (task-489/LB-18, task-614/LC-17, 30ms)과 같은 자릿수 안에 있음을
    증명한다."""
    symbol = _krx_symbol()
    listed_at = datetime.now(timezone.utc) - timedelta(days=1)
    async with pool.acquire() as conn, conn.transaction():
        await repo.register(conn, _register_cmd(venue_symbol=symbol, listed_at=listed_at))

    n = 200
    budget_p95_sec = 0.03
    at = datetime.now(timezone.utc)
    latencies: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            found = await repo.get_instrument(conn, Venue.KIS_KRX, symbol, at)
        latencies.append(time.perf_counter() - start)
        assert found is not None

    latencies.sort()
    p95 = latencies[int(n * 0.95)]
    print(
        f"[LA-12 get_instrument] n={n} p95={p95 * 1000:.2f}ms "
        f"(budget<{budget_p95_sec * 1000:.0f}ms)"
    )
    assert p95 < budget_p95_sec, f"get_instrument p95가 예산을 넘었습니다: {p95:.4f}s"


async def test_gate_red_when_digest_check_removed_negative_test_would_fail(pool, repo, monkeypatch):
    """게이트 적색 재현: record_action의 다이제스트 불일치 검사(fail-closed,
    §9.2 LA-12)를 제거하는 회귀를 주입하면 탬퍼가 조용히 통과한다 —
    test_record_action_different_content_same_key_raises가 지키는
    `pytest.raises(CorporateActionDigestMismatchError)` 블록이 그 회귀
    아래에서 green에서 red로 뒤집힘을 이 자리에서 직접 재현한다."""
    symbol = _krx_symbol()
    listed_at = datetime.now(timezone.utc) - timedelta(days=30)
    async with pool.acquire() as conn, conn.transaction():
        instrument = await repo.register(
            conn, _register_cmd(venue_symbol=symbol, listed_at=listed_at)
        )

    ex_date = listed_at.date() + timedelta(days=1)
    source_ref = f"test:{uuid.uuid4().hex}"
    action = CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument.instrument_id,
        ex_date=ex_date,
        ratio=Decimal("2"),
        source_ref=source_ref,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.record_action(conn, action)

    conflicting = action.model_copy(update={"ratio": Decimal("3")})

    async def _regressed_record_action(
        self: PostgresReferenceRepository, conn: asyncpg.Connection, action: CorporateAction
    ) -> CorporateAction:
        existing = await conn.fetchrow(
            "SELECT * FROM md_corporate_action "
            "WHERE instrument_id = $1 AND action_type = $2 AND ex_date = $3",
            action.instrument_id,
            action.action_type,
            action.ex_date,
        )
        assert existing is not None, "이 시나리오는 항상 기존 행이 있어야 한다"
        # 회귀: ratio/cash_amount/source_ref 다이제스트 비교 없이 조용히
        # 기존 값을 그대로 반환한다 — 탬퍼를 탐지하지 못한다.
        return CorporateAction(
            action_type=existing["action_type"],
            instrument_id=existing["instrument_id"],
            ex_date=existing["ex_date"],
            ratio=existing["ratio"],
            cash_amount=existing["cash_amount"],
            source_ref=existing["source_ref"],
        )

    monkeypatch.setattr(PostgresReferenceRepository, "record_action", _regressed_record_action)

    with pytest.raises(pytest.fail.Exception):
        with pytest.raises(CorporateActionDigestMismatchError):
            async with pool.acquire() as conn, conn.transaction():
                await repo.record_action(conn, conflicting)
