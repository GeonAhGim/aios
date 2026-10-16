"""PostgresBalanceRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-8.
DoD(task-320): "expected_seq 불일치 negative" 필수.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.ledger.adapters.postgres_balance_repository import (
    PostgresBalanceRepository,
    UnknownAccountError,
)
from tests.integration.foundation.ledger.conftest import create_ledger_account


@pytest.fixture
def repo(pool):
    return PostgresBalanceRepository(pool)


async def test_get_for_update_returns_view_with_available_computed(pool, repo):
    code = await create_ledger_account(
        pool, initial_balance=Decimal("100.00"), initial_held=Decimal("30.00")
    )

    async with pool.acquire() as conn, conn.transaction():
        result = await repo.get_for_update(conn, [code])

    view = result[code]
    assert view.balance == Decimal("100.00")
    assert view.held == Decimal("30.00")
    assert view.available == Decimal("70.00")
    assert view.last_entry_seq == 0


async def test_get_for_update_raises_on_unknown_account(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(UnknownAccountError):
            await repo.get_for_update(conn, ["PLATFORM:DOES_NOT_EXIST"])


async def test_get_for_update_empty_list_returns_empty_dict(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        result = await repo.get_for_update(conn, [])
    assert result == {}


async def test_apply_updates_balance_and_advances_seq(pool, repo):
    code = await create_ledger_account(pool, initial_balance=Decimal("50.00"))

    async with pool.acquire() as conn, conn.transaction():
        view = await repo.apply(conn, code, Decimal("25.00"), Decimal("0.00"), expected_seq=0)

    assert view.balance == Decimal("75.00")
    assert view.last_entry_seq == 1


async def test_apply_second_call_uses_advanced_seq(pool, repo):
    code = await create_ledger_account(pool, initial_balance=Decimal("50.00"))

    async with pool.acquire() as conn, conn.transaction():
        await repo.apply(conn, code, Decimal("10.00"), Decimal("0.00"), expected_seq=0)
    async with pool.acquire() as conn, conn.transaction():
        second = await repo.apply(conn, code, Decimal("5.00"), Decimal("0.00"), expected_seq=1)

    assert second.balance == Decimal("65.00")
    assert second.last_entry_seq == 2


async def test_apply_rejects_stale_expected_seq(pool, repo):
    """DoD: expected_seq 불일치 negative — 이미 seq가 전진했는데 옛 값으로
    다시 apply하면 ConcurrencyConflictError(재조회 후 재시도 필요)."""
    code = await create_ledger_account(pool, initial_balance=Decimal("50.00"))

    async with pool.acquire() as conn, conn.transaction():
        await repo.apply(conn, code, Decimal("10.00"), Decimal("0.00"), expected_seq=0)

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(ConcurrencyConflictError):
            await repo.apply(conn, code, Decimal("5.00"), Decimal("0.00"), expected_seq=0)


async def test_apply_raises_on_unknown_account(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(UnknownAccountError):
            await repo.apply(
                conn, "PLATFORM:DOES_NOT_EXIST", Decimal("1.00"), Decimal("0.00"), expected_seq=0
            )


@pytest.mark.perf
async def test_apply_p95_latency_within_budget(pool, repo, perf_budget):
    """성능 단언: `apply`(조건부 UPDATE + seq 전진, 105 표준 쓰기 경로)는
    ADR-2026-09-09-C Decision 1 원장 포스팅 계열 예산과 동급 규모의 단일
    행 쓰기이므로 같은 p95 < 50ms 상한을 쓴다.

    CI `--cov=src` 단계에서는 coverage.py 트레이서 오버헤드 + 러너 CPU
    경합으로 벽시계 wall_ms가 lokal보다 2~3배 느려질 수 있다. 따라서
    단언 상한을 100ms로 넓히되 — 목적은 회귀(실수로 I/O 루프가 끼워든
    경우 등)를 잡는 것이므로 100ms 초과 시 여전히 실패한다(task-15098
    패턴)."""
    code = await create_ledger_account(pool, initial_balance=Decimal("1000.00"))

    samples: list[float] = []
    seq = 0
    for _ in range(20):
        expected_seq = seq

        async def _call(expected_seq=expected_seq):
            async with pool.acquire() as conn, conn.transaction():
                return await repo.apply(
                    conn, code, Decimal("1.00"), Decimal("0.00"), expected_seq=expected_seq
                )

        measured = await perf_budget.sample_async(_call)
        samples.append(measured.wall_ms)
        seq = measured.result.last_entry_seq

    samples.sort()
    p95_ms = samples[int(len(samples) * 0.95) - 1]
    assert p95_ms < 100, f"apply p95={p95_ms:.1f}ms exceeds 100ms budget"
