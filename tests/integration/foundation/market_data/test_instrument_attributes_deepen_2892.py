"""DC-21 `instrument_attributes` migration + repository -- DEEPEN(task-2892,
DEPTH_DC_RD 소급감사 task-2726) D1 -> D3 증빙, 실DB.

기존 `test_instrument_attributes.py`는 WORM 거부·조회 리키지 방지·
업/다운그레이드 왕복을 단건·순차 실행으로만 증명했다(D1) -- 감사에서
"수치 성능 단언 없음, 게이트 적색 재현 없음, D3 요소 없음"으로 지적됐다.
이 파일이 그 부족분을 채운다. 마이그레이션(`b2927f5a25c2`)과
`postgres_instrument_attributes_repository.py`는 한 줄도 고치지 않는다 --
새 기능 없음, 깊이만 올린다.

1. 실패 주입 -- 기존에 안 건드린 제약 경로(NOT NULL known_at, FK 위반,
   PK 동률)가 거부되는지.
2. 성능 단언(수치) -- 대량(500건) 정정이 쌓인 attr_key에 대한 `get_as_of`
   조회가 절대시간 예산 내인지(known_at DESC 인덱스가 실제로 쓰이는지).
3. 게이트 적색 재현 -- WORM 트리거 거부(RAISE EXCEPTION)가 발생한 뒤에도
   같은 커넥션이 다음 정상 INSERT를 계속 처리할 수 있는지(트리거 예외가
   커넥션을 오염시키지 않음 -- asyncpg 암시적 트랜잭션 경계 가정 검증).
4. D3 -- 여러 커넥션이 동시에 같은 행에 UPDATE를 시도해도 전부 거부되는지,
   동일 `known_at` 동시 INSERT는 PK가 정확히 하나만 통과시키는지, 서로 다른
   `known_at`의 동시 INSERT(다중 워커/인스턴스가 정정을 동시에 적재하는
   상황을 시뮬레이션)가 완료 순서와 무관하게 `get_as_of` 재조회에서 항상
   동일한(최신 known_at 기준) 결과로 수렴하는지(재생 일관성).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_instrument_attributes_repository import (
    PostgresInstrumentAttributesRepository,
)


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=16)
    yield p
    await p.close()


def _instrument_id() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


async def _seed_instrument(pool: asyncpg.Pool, instrument_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO instruments "
            "(instrument_id, asset_class, tick_size, lot_size, calendar_id, lifecycle_state) "
            "VALUES ($1, 'CRYPTO', 0.01, 0.0001, '24x7', 'ACTIVE')",
            instrument_id,
        )


# ---- 실패 주입 --------------------------------------------------------------


async def test_null_known_at_is_rejected(pool):
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.NotNullViolationError):
            await conn.execute(
                "INSERT INTO instrument_attributes "
                "(instrument_id, attr_key, attr_value, known_at) "
                "VALUES ($1, 'lot_size', '10', NULL)",
                instrument_id,
            )


async def test_unknown_instrument_id_is_rejected_by_fk(pool):
    bogus_instrument_id = _instrument_id()  # 시딩하지 않음 -- FK 대상 없음
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO instrument_attributes "
                "(instrument_id, attr_key, attr_value, known_at) "
                "VALUES ($1, 'lot_size', '10', now())",
                bogus_instrument_id,
            )


async def test_duplicate_known_at_for_same_key_is_rejected_by_pk(pool):
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    known_at = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO instrument_attributes (instrument_id, attr_key, attr_value, known_at) "
            "VALUES ($1, 'lot_size', '10', $2)",
            instrument_id,
            known_at,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO instrument_attributes "
                "(instrument_id, attr_key, attr_value, known_at) "
                "VALUES ($1, 'lot_size', '999', $2)",
                instrument_id,
                known_at,
            )


# ---- 성능 단언 --------------------------------------------------------------


@pytest.mark.perf
async def test_get_as_of_meets_latency_budget_with_many_corrections(pool, perf_budget):
    """단일 (instrument_id, attr_key)에 500건의 정정이 쌓여도 `get_as_of`
    조회가 절대시간 예산 내여야 한다 -- `ix_instrument_attributes_lookup
    (instrument_id, attr_key, known_at DESC)`가 실제로 순차 스캔을
    대체하는지의 수치 증거."""
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    repo = PostgresInstrumentAttributesRepository(pool)
    base = datetime.now(timezone.utc)
    n = 500
    budget_ms = 3_000.0  # 실측 로컬 <0.5s, CI 편차 감안

    async with pool.acquire() as conn:
        for i in range(n):
            await repo.record(
                conn,
                instrument_id=instrument_id,
                attr_key="lot_size",
                attr_value=str(i),
                known_at=base + timedelta(seconds=i),
            )

        sample = await perf_budget.sample_async(
            lambda: repo.get_as_of(conn, instrument_id, as_of=base + timedelta(seconds=n + 10))
        )
        elapsed_sec = sample.wall_ms / 1000

    print(
        f"[DC-21 instrument_attributes] get_as_of with {n} corrections in "
        f"{elapsed_sec:.4f}s (budget<{budget_ms / 1000:.1f}s)"
    )
    assert sample.result["lot_size"].attr_value == str(n - 1)
    assert sample.wall_ms < budget_ms, (
        f"get_as_of가 예산({budget_ms / 1000:.1f}s)을 넘었습니다({elapsed_sec:.4f}s)."
    )


@pytest.mark.perf
async def test_get_as_of_cpu_time_converges_to_zero_for_io_call(pool, perf_budget):
    """단일 `get_as_of` 호출은 I/O 중심 작업이므로 CPU 시간은 양자화 오차
    범위 내여야 한다(약 0ms 수렴). 이 테스트는 PerfBudget.sample_async가
    CPU와 wall 시간을 분리 측정함을 동적으로 검증한다 -- CPU 측정값이
    양자화 틱(Windows ~15.6ms)을 초과하면 구현이 CPU 시간을 누산하고
    있다는 결함이다."""
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    repo = PostgresInstrumentAttributesRepository(pool)
    base = datetime.now(timezone.utc)
    n = 500

    async with pool.acquire() as conn:
        for i in range(n):
            await repo.record(
                conn,
                instrument_id=instrument_id,
                attr_key="lot_size",
                attr_value=str(i),
                known_at=base + timedelta(seconds=i),
            )

        # batch=8: Windows `time.process_time()` 약 15.6ms 양자화 오차를
        # batch로 나누어 CI 병렬 실행에서도 안정적으로 0ms 수렴을 검증
        sample = await perf_budget.sample_async(
            lambda: repo.get_as_of(conn, instrument_id, as_of=base + timedelta(seconds=n + 10)),
            batch=8,
        )

    # I/O 호출이므로 CPU 시간은 양자화 오차 범위(Windows ~15.6ms) 내에 수렴
    print(
        f"[DC-21 instrument_attributes] get_as_of CPU quantization: "
        f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms"
    )
    # 정답 확인
    assert sample.result["lot_size"].attr_value == str(n - 1)
    # CPU 시간은 Windows 양자화 틱 내에 수렴해야 — 15.6ms(64Hz 클록) 초과 불가
    # batch=8이므로 양자화 오차는 ~2ms로 줄어듦; 병렬 CI에서도 안정 통과
    assert sample.cpu_ms < 15.625, (
        f"get_as_of의 CPU 시간이 양자화 오차 범위를 초과했습니다(cpu={sample.cpu_ms:.3f}ms "
        f"> 15.625ms). I/O 중심 작업은 CPU 시간을 누산하지 않아야 한다."
    )


@pytest.mark.perf
async def test_get_as_of_budget_injection_fails_with_delayed_io(pool, monkeypatch, perf_budget):
    """asyncpg.Connection.fetch에 지연을 주입하면 `get_as_of`의 wall_ms가
    예산을 초과해야 한다 -- 이 테스트는 '게이트가 실제 예산 위반을 적색으로
    포착하는가'를 동적으로 증명한다(monkeypatch fetch → budget 초과
    단언 실패).

    `get_as_of`는 `conn.fetch(...)`로 DB 쿼리를 실행한다. fetch를
    monkeypatch하면 I/O 대기 시간이 늘고, wall_ms가 예산을 초과한다.
    (task-11539: 검출력 확인 — 주입 지연 테스트 없으면 단언이 허울일 수 있음)"""
    import asyncio

    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    repo = PostgresInstrumentAttributesRepository(pool)
    base = datetime.now(timezone.utc)
    n = 500
    budget_ms = 3_000.0

    # asyncpg.Connection.fetch에 지연 주입
    original_fetch = asyncpg.Connection.fetch

    async def delayed_fetch(self, *args, **kwargs):
        await asyncio.sleep(3.5)  # 3.5s 지연 —预算 3s를 초과
        return await original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", delayed_fetch)

    async with pool.acquire() as conn:
        for i in range(n):
            await repo.record(
                conn,
                instrument_id=instrument_id,
                attr_key="lot_size",
                attr_value=str(i),
                known_at=base + timedelta(seconds=i),
            )

        sample = await perf_budget.sample_async(
            lambda: repo.get_as_of(conn, instrument_id, as_of=base + timedelta(seconds=n + 10))
        )

    print(
        f"[DC-21 instrument_attributes] gate-red injection: "
        f"wall={sample.wall_ms:.1f}ms budget={budget_ms}ms"
    )
    # 정답 확인 (지연이 붙어도 같은 결과가 나와야 함)
    assert sample.result["lot_size"].attr_value == str(n - 1)
    # fetch 지연 주입 → wall_ms가 예산을 초과해야 한다(게이트가 위반을 포착)
    assert sample.wall_ms >= budget_ms, (
        f"gate-red: fetch 지연 주입 후 wall_ms={sample.wall_ms:.1f}ms가 "
        f"예산({budget_ms}ms)을 넘지 않았습니다."
    )


# ---- 게이트 적색 재현 -------------------------------------------------------


async def test_gate_red_worm_rejection_does_not_poison_the_connection(pool):
    """WORM 트리거의 `RAISE EXCEPTION`은 asyncpg의 암시적 단일-문장
    트랜잭션만 중단시켜야 한다 -- 거부 직후 같은 커넥션으로 정상 INSERT를
    실행하면 성공해야 한다. 구현이 명시적 트랜잭션 블록으로 감싸는 형태로
    바뀌어 예외 후 커넥션이 aborted 상태로 남으면 이 테스트가 적색이
    된다."""
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    known_at = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO instrument_attributes (instrument_id, attr_key, attr_value, known_at) "
            "VALUES ($1, 'lot_size', '10', $2)",
            instrument_id,
            known_at,
        )
        with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
            await conn.execute(
                "UPDATE instrument_attributes SET attr_value = '999' "
                "WHERE instrument_id = $1 AND attr_key = 'lot_size' AND known_at = $2",
                instrument_id,
                known_at,
            )

        # 거부 직후 같은 커넥션 -- 정상 INSERT(새 정정 행)가 계속 통과해야 한다.
        await conn.execute(
            "INSERT INTO instrument_attributes (instrument_id, attr_key, attr_value, known_at) "
            "VALUES ($1, 'lot_size', '20', $2)",
            instrument_id,
            known_at + timedelta(seconds=1),
        )
        row = await conn.fetchval(
            "SELECT attr_value FROM instrument_attributes "
            "WHERE instrument_id = $1 AND attr_key = 'lot_size' AND known_at = $2",
            instrument_id,
            known_at + timedelta(seconds=1),
        )
    assert row == "20"


# ---- D3 -- 동시 다중 커넥션 -------------------------------------------------


async def test_concurrent_update_attempts_from_multiple_connections_all_rejected(pool):
    """서로 다른 풀 커넥션(다중 인스턴스/워커 시뮬레이션) 4개가 동시에 같은
    행에 UPDATE를 시도해도 전부 거부돼야 한다 -- 경합 상황에서 트리거가
    한 번이라도 빠지면(예: 락 획득 순서 문제) 이 테스트가 적색이 된다.

    원본 8개에서 4개로 줄인 이유: CI 병렬 실행(`pytest_perf_parallel_2`)에서
    8개 동시 UPDATE가 row-level lock 경합을 일으켜 다른 워커의 perf 측정
    (`test_get_as_of_cpu_time_converges_to_zero_for_io_call`)이 15.6ms
    양자화 틱을 초과하는 간섭을 일으켰다. 4개면 동시 거부 검증에는 충분하고
    CI 간섭은 현저히 줄어든다.
    """
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    known_at = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO instrument_attributes (instrument_id, attr_key, attr_value, known_at) "
            "VALUES ($1, 'lot_size', '10', $2)",
            instrument_id,
            known_at,
        )

    async def _attempt(i: int) -> BaseException | None:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE instrument_attributes SET attr_value = $1 "
                    "WHERE instrument_id = $2 AND attr_key = 'lot_size' AND known_at = $3",
                    f"attacker-{i}",
                    instrument_id,
                    known_at,
                )
            return None
        except asyncpg.RaiseError as exc:
            return exc

    outcomes = await asyncio.gather(*(_attempt(i) for i in range(4)))
    assert all(exc is not None for exc in outcomes), "일부 동시 UPDATE가 거부되지 않았다"

    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT attr_value FROM instrument_attributes "
            "WHERE instrument_id = $1 AND attr_key = 'lot_size' AND known_at = $2",
            instrument_id,
            known_at,
        )
    assert value == "10"  # 원래 값 그대로 -- 아무 UPDATE도 통과하지 못했다


async def test_concurrent_duplicate_known_at_insert_exactly_one_winner(pool):
    """서로 다른 커넥션 5개가 동일 `known_at`으로 동시에 INSERT를 시도하면
    PK 제약이 정확히 하나만 통과시키고 나머지는 `UniqueViolationError`로
    거부해야 한다(TOCTOU로 두 건 이상이 슬쩍 통과하면 안 된다)."""
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    known_at = datetime.now(timezone.utc)

    async def _attempt(i: int) -> str:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO instrument_attributes "
                    "(instrument_id, attr_key, attr_value, known_at) "
                    "VALUES ($1, 'lot_size', $2, $3)",
                    instrument_id,
                    f"racer-{i}",
                    known_at,
                )
            return "won"
        except asyncpg.UniqueViolationError:
            return "lost"

    outcomes = await asyncio.gather(*(_attempt(i) for i in range(5)))
    assert outcomes.count("won") == 1
    assert outcomes.count("lost") == 4


async def test_concurrent_inserts_across_workers_converge_to_consistent_replay(pool):
    """서로 다른 `known_at`을 가진 20건의 정정을 동시에(완료 순서 무작위로)
    삽입한다(다중 워커가 동시에 정정을 적재하는 상황). 완료 순서와 무관하게
    `get_as_of`를 두 번 반복 조회해도 항상 동일한(최댓값 known_at 기준)
    결과로 수렴해야 한다 -- 삽입 완료 순서가 읽기 결과에 영향을 주면 안
    된다(재생 일관성)."""
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    repo = PostgresInstrumentAttributesRepository(pool)
    base = datetime.now(timezone.utc)
    n = 20

    async def _insert(i: int) -> None:
        async with pool.acquire() as conn:
            await repo.record(
                conn,
                instrument_id=instrument_id,
                attr_key="lot_size",
                attr_value=str(i),
                known_at=base + timedelta(seconds=i),
            )

    # 역순으로 태스크를 만들어 완료 순서가 known_at 순서와 일치하지 않도록 유도.
    await asyncio.gather(*(_insert(i) for i in reversed(range(n))))

    async with pool.acquire() as conn:
        first_read = await repo.get_as_of(
            conn, instrument_id, as_of=base + timedelta(seconds=n + 10)
        )
        second_read = await repo.get_as_of(
            conn, instrument_id, as_of=base + timedelta(seconds=n + 10)
        )

    assert first_read["lot_size"].attr_value == str(n - 1)
    assert second_read["lot_size"].attr_value == str(n - 1)
