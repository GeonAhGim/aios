"""R-31 `risk_inputs_assembler.py` 통합테스트 — 실 DB 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.2, §3.5, §9 R-31.

negative/실패주입/성능 D3 증빙은 `test_risk_inputs_assembler_deepen.py`
(task-10539, loc_over_500 분리)에 있다.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from src.core.risk.decision import RiskOutcome
from src.core.risk.rules import safety_state
from src.data.models.trading import AccountBalance
from src.services.execution_loop.risk_inputs_assembler import (
    assemble_risk_inputs,
    to_legacy_dict,
)
from tests.integration.conftest import create_test_user
from tests.integration.risk._risk_inputs_assembler_fixtures import (
    NOW,
    POLICY,
    asyncpg_dsn,
    caches,
    candles,
    create_execution,
    insert_position,
    intent,
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


async def test_two_select_round_trips(pool, monkeypatch):
    """DoD (1) — assemble_risk_inputs 1회 호출당 SELECT 실행 ≤ 2회
    (§3.5 CTE 스냅샷 1회 + read_fences 1회). equity 기준점은 미리 seed해
    R-30의 최초-1회 SELECT가 이 카운트에 섞이지 않게 한다(그 SELECT는
    이 리프가 만드는 새 왕복이 아니라 R-30이 이미 책임지는 경로다)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"

    queries: list[str] = []
    original_fetchrow = asyncpg.Connection.fetchrow
    original_fetch = asyncpg.Connection.fetch

    async def counting_fetchrow(self, query, *args, **kwargs):
        queries.append(query)
        return await original_fetchrow(self, query, *args, **kwargs)

    async def counting_fetch(self, query, *args, **kwargs):
        queries.append(query)
        return await original_fetch(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", counting_fetchrow)
    monkeypatch.setattr(asyncpg.Connection, "fetch", counting_fetch)

    riskcaches = caches()
    riskcaches.equity_tracker.seed(
        execution_id,
        day_start_date=date(2026, 1, 1),
        day_start_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
    )
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]

    await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-count"),
        balances=balances,
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )

    select_count = sum(1 for q in queries if q.strip().upper().startswith(("SELECT", "WITH")))
    assert select_count == 2, queries


async def test_fields_filled_or_none_not_defaulted(pool):
    """DoD (2) — 계산 가능한 필드는 실제 값을, 이 리프의 2왕복 예산 안에서
    관측할 수 없는 필드는 0/False로 뭉개지 않고 명시적 None을 반환한다."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    # 유일 심볼 — 공유 테스트 DB에서 실제 check_and_persist_distrust를 타는
    # test_execution_tick.py가 bitget+BTC/USDT에 진짜 data_distrust_state
    # 행을 남기면 아래 None 단언이 실행 순서에 따라 깨진다(재현 확인됨).
    symbol = f"FIELDS-{uuid.uuid4().hex[:8]}/USDT"
    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]

    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-fields"),
        balances=balances,
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )

    # 실제로 계산되는 값 — 0/False로 뭉개지지 않았다.
    assert inputs.equity.total_equity == Decimal("1000")
    assert inputs.exposure.open_positions_count == 0
    assert inputs.exposure.position_quantity == Decimal("0")

    # 이 리프의 2왕복 예산으로는 관측 불가능한 값 — None(fail-closed), 기본값 아님.
    assert inputs.safety.active_control_scopes is None
    assert inputs.safety.distrust_sources_available is None
    assert inputs.safety.connection_fresh is None
    assert inputs.safety.rule_bundle_active is None
    assert inputs.equity.account_daily_pnl_pct is None
    assert inputs.equity.account_drawdown_pct is None
    # data_distrust_state에 이 exchange+symbol 행이 없다 — None(0/NORMAL로 위장 안 함).
    assert inputs.safety.data_distrust_level is None


async def test_missing_distrust_level_denies_via_safety_state_rule(pool):
    """DoD (2) 네거티브 — distrust_level 행 없음 → None → safety_state
    규칙이 실제로 DENY함을 재현한다(조용히 통과 금지, I2)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "ETH/USDT"
    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]

    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-distrust"),
        balances=balances,
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )
    assert inputs.safety.data_distrust_level is None

    # active_control_scopes는 이 조립기 예산 밖이라 항상 None이다 — safety_state
    # 규칙이 그 필드보다 먼저 결손 처리해버리면 distrust_level 경로를 가릴 수
    # 있으므로, 이 테스트가 검증하려는 필드(data_distrust_level)만 격리한다.
    isolated = inputs.model_copy(
        update={
            "safety": inputs.safety.model_copy(update={"active_control_scopes": ()}),
        }
    )

    result = safety_state.safety_state(isolated, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_INPUT_MISSING:safety.data_distrust_level"


async def test_other_tenants_positions_excluded(pool):
    """DoD (6) — 교차 테넌트: user_b의 포지션이 user_a의 조립 결과로 새지
    않는다."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    execution_a = await create_execution(pool, user_a)
    symbol = "SOL/USDT"
    strategy_id = "strat-isolated"

    await insert_position(
        pool,
        user_id=user_a,
        symbol=symbol,
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("2"),
        average_entry_price=Decimal("100"),
    )
    await insert_position(
        pool,
        user_id=user_b,
        symbol=symbol,
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("999"),
        average_entry_price=Decimal("100"),
    )

    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]
    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_a,
        user_id=user_a,
        intent=intent(symbol, strategy_id),
        balances=balances,
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )

    assert inputs.exposure.position_quantity == Decimal("2")
    assert inputs.exposure.gross_notional[f"TENANT:{user_a}"] == Decimal("200")


async def test_to_legacy_dict_round_trips_fourteen_keys(pool):
    """account_state.py가 기대하는 14키가 그대로 채워진다(무회귀)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"
    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]

    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-legacy"),
        balances=balances,
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )
    legacy = to_legacy_dict(inputs)

    assert set(legacy) == {
        "daily_pnl_pct",
        "drawdown_pct",
        "position_quantity",
        "total_equity",
        "certified_badge",
        "allocated_capital",
        "available_balance",
        "var_pct",
        "correlated_exposure_pct",
        "recent_trade_count_1h",
        "avg_trade_count_24h",
        "circuit_breaker_level",
        "execution_paused_by_safety",
        "leverage",
    }
    assert legacy["leverage"] == Decimal("1")  # 열린 포지션 없음 — 무레버리지 기본값
    assert legacy["total_equity"] == Decimal("1000")
