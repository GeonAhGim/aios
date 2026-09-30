"""R-27 exposure_snapshot.py 통합테스트 — negative/실패주입/D3 (실 DB 대상).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.5, §9 R-27.

RATCHET-split(task-10202): test_exposure_snapshot.py의 571줄 중 negative
(닫힌 포지션/0수량 필터), 실패 주입(커넥션 오류 비삼킴), 동시 테넌트 격리
(D3), 게이트 적색 재현을 이 파일로 분리했다 — 책임 축이 "정상 스코프 계산"과
다른 "결함/장애 상황에서의 안전성 증명"이라 별도 파일로 나누는 것이 맞다.
fixture/헬퍼는 _exposure_snapshot_fixtures.py 공유 모듈을 그대로 쓴다."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

import src.services.execution_loop.exposure_snapshot as exposure_snapshot_mod
from src.services.execution_loop.exposure_snapshot import load_exposure_snapshot
from tests.integration.conftest import create_test_user
from tests.integration.services._exposure_snapshot_fixtures import (
    _create_execution,
    _insert_position,
    pool,
)

__all__ = ["pool"]


async def test_closed_position_is_excluded_from_exposure(pool):
    """negative — §3.5 open_pos CTE의 `closed_at IS NULL` 필터가 실제로
    청산된(quantity는 남아 있되 closed_at이 찍힌) 포지션을 노출 합계에서
    빼는지 실DB로 증명한다(09번 §9.1 #9: 청산 시 행을 지우지 않고
    closed_at만 채우므로, 이 필터가 없으면 청산 포지션이 살아있는
    노출로 이중 집계된다)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    strategy_id = "strat-closed"
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("2"),
        average_entry_price=Decimal("40000"),
    )
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("5"),
        average_entry_price=Decimal("40000"),
        closed_at=datetime.now(timezone.utc),
    )

    async with pool.acquire() as conn:
        snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_id,
            execution_id=execution_id,
            symbol="BTC/USDT",
            strategy_id=strategy_id,
            provider="bitget",
            prices={"BTC/USDT": Decimal("50000")},
        )

    assert snapshot.position_quantity == Decimal("2")
    assert snapshot.open_positions_count == 1
    assert snapshot.gross_tenant == Decimal("2") * Decimal("50000")


async def test_zero_quantity_position_is_excluded_from_exposure(pool):
    """negative — §3.5 open_pos CTE의 `quantity <> 0` 필터 실증. 청산 직전
    잔여 0수량 행이 남아 있어도 노출·건수에 잡히면 안 된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")
    strategy_id = "strat-zero-qty"
    await _insert_position(
        pool,
        user_id=user_id,
        symbol="ETH/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("0"),
        average_entry_price=Decimal("2000"),
    )

    async with pool.acquire() as conn:
        snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_id,
            execution_id=execution_id,
            symbol="ETH/USDT",
            strategy_id=strategy_id,
            provider="bitget",
            prices={},
        )

    assert snapshot.open_positions_count == 0
    assert snapshot.gross_tenant == Decimal("0")
    assert snapshot.gross_asset_class == {}


async def test_connection_failure_propagates_instead_of_being_swallowed(pool, monkeypatch):
    """실패 주입 — DB 커넥션 오류가 나면 예외를 삼키지 않고 그대로
    전파해야 한다. 호출부(risk_inputs_assembler.assemble_risk_inputs)가
    실패를 "노출 0"으로 오인하면 사전 리스크 게이트가 실제 포지션을 못 본
    채 통과시키는 fail-open이 된다(I-06 위반)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, exchange="bitget")

    async def raising_fetchrow(self, *args, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", raising_fetchrow)

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await load_exposure_snapshot(
                conn,
                user_id=user_id,
                execution_id=execution_id,
                symbol="BTC/USDT",
                strategy_id="strat-fail",
                provider="bitget",
                prices={"BTC/USDT": Decimal("50000")},
            )


async def test_concurrent_snapshot_reads_for_different_tenants_stay_isolated(pool):
    """다중 인스턴스 경합(D3) — 서로 다른 커넥션 위에서 두 테넌트가 실제로
    겹쳐서(asyncio.gather) 노출 스냅샷을 조회해도, 조건절 파라미터 바인딩이
    커넥션 간에 뒤섞이거나 캐시되지 않고 각자 자기 테넌트 값만 본다."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    execution_a = await _create_execution(pool, user_a, exchange="bitget")
    execution_b = await _create_execution(pool, user_b, exchange="bitget")
    strategy_id = "strat-concurrent"

    await _insert_position(
        pool,
        user_id=user_a,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("1"),
        average_entry_price=Decimal("10000"),
    )
    await _insert_position(
        pool,
        user_id=user_b,
        symbol="BTC/USDT",
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("777"),
        average_entry_price=Decimal("10000"),
    )

    async def _load(user_id: UUID, execution_id: int) -> object:
        async with pool.acquire() as conn:
            return await load_exposure_snapshot(
                conn,
                user_id=user_id,
                execution_id=execution_id,
                symbol="BTC/USDT",
                strategy_id=strategy_id,
                provider="bitget",
                prices={"BTC/USDT": Decimal("10000")},
            )

    snapshot_a, snapshot_b = await asyncio.gather(
        _load(user_a, execution_a), _load(user_b, execution_b)
    )

    assert snapshot_a.position_quantity == Decimal("1")
    assert snapshot_b.position_quantity == Decimal("777")


async def test_regression_confirms_suite_would_catch_missing_tenant_filter(pool, monkeypatch):
    """게이트 적색 재현 — SQL의 `p.user_id = $1` 술어가 실수로 빠지면(다른
    파라미터 자리로 밀리거나 리팩터링 중 삭제되는 배선 결함) 이 스위트가
    실제로 실패(RED)함을 먼저 증명한 뒤, 원래 SQL로 되돌려 통과(GREEN)를
    재확인한다 — "테스트가 있다"가 아니라 "테스트가 이 결함을 실제로
    잡는다"를 증명한다."""
    # 심볼을 매 실행마다 새로 만든다 — 이 공유 통합 테스트 DB는 함수 간
    # 롤백이 없어(다른 케이스들도 고정 심볼을 재사용) 고정 심볼을 쓰면
    # 이전 테스트/실행에서 남은 행까지 섞여 아래 등식 단언이 깨진다.
    symbol = f"GATERED-{uuid.uuid4().hex[:8]}/USDT"
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    execution_a = await _create_execution(pool, user_a, exchange="bitget")
    strategy_id = "strat-gate-red"

    await _insert_position(
        pool,
        user_id=user_a,
        symbol=symbol,
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("10"),
        average_entry_price=Decimal("100"),
    )
    await _insert_position(
        pool,
        user_id=user_b,
        symbol=symbol,
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("999"),
        average_entry_price=Decimal("100"),
    )

    original_sql = exposure_snapshot_mod._SQL
    assert "p.user_id = $1" in original_sql
    # "$1::uuid IS NOT NULL"로 바꿔 파라미터 타입 추론은 유지하면서(항상
    # 참이 되어) 실제 테넌트 필터링만 무력화한다 — 단순 "TRUE" 치환은
    # $1이 쿼리에서 안 쓰이게 되어 asyncpg가 타입을 추론하지 못해 다른
    # 예외(IndeterminateDatatypeError)로 죽으므로 실제 배선 결함과
    # 다른 실패 모드를 재현하게 된다.
    broken_sql = original_sql.replace("p.user_id = $1", "$1::uuid IS NOT NULL")
    monkeypatch.setattr(exposure_snapshot_mod, "_SQL", broken_sql)

    async with pool.acquire() as conn:
        broken_snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_a,
            execution_id=execution_a,
            symbol=symbol,
            strategy_id=strategy_id,
            provider="bitget",
            prices={symbol: Decimal("100")},
        )
    # 결함 주입 상태 — user_b의 포지션까지 새어 들어온다(RED 재현).
    assert broken_snapshot.position_quantity == Decimal("10") + Decimal("999")
    monkeypatch.undo()

    async with pool.acquire() as conn:
        fixed_snapshot = await load_exposure_snapshot(
            conn,
            user_id=user_a,
            execution_id=execution_a,
            symbol=symbol,
            strategy_id=strategy_id,
            provider="bitget",
            prices={symbol: Decimal("100")},
        )
    # 원상복구 후: 실제 구현은 user_a만 본다(GREEN 재확인).
    assert fixed_snapshot.position_quantity == Decimal("10")
