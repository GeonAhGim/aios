"""FD-8 실행 루프 통합테스트 공용 픽스처/헬퍼 — test_execution_tick*.py
분할 파일들이 공유한다(ADR-2026-09-10-C LOC 규율, task-10199).
"""

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
from dotenv import dotenv_values

from src.core.executor.executor import Executor
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.portfolio.engine import PortfolioEngine
from src.core.risk.engine import RiskEngine
from src.core.safety.data_distrust import DataDistrustMonitor
from src.core.strategy.engine import StrategyEngine
from src.services.condition_compiler import ConditionCompiler
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.preview_service import PreviewCondition


async def _allow_gate(context: object) -> GateDecision:
    """이 파일은 FD-8 파이프라인(신호→배분→리스크→실행)을 검증한다 —
    게이트 배선 자체는 tests/adversarial/risk/가 맡는다(task-1715/P0-B가
    `run_execution_tick`의 `pre_submit_gate`를 필수 인자로 바꾼 뒤에도 이
    파일의 기존 시나리오가 우회 없이 그대로 통과하게 하는 대역)."""
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _make_pool() -> asyncpg.Pool:
    """각 분할 파일이 자기 `pool` 픽스처에서 호출한다 — 픽스처 자체를
    공유 임포트하면 테스트 함수 인자명 `pool`과 충돌해 ruff F811이
    발생하므로(task-10199), 픽스처 선언은 파일마다 두고 생성 로직만
    공유한다."""
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        # system_safety_state는 전역 싱글톤 행이라 다른 테스트 파일(예:
        # test_circuit_breaker.py)이 남긴 상태에 오염될 수 있다 — 그 파일의
        # 격리 관례를 그대로 따른다.
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    return p


def _fsm_definition(*, entry_threshold: float) -> dict:
    compiled = ConditionCompiler().compile(
        strategy_id="tick-test",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[
            PreviewCondition(
                indicator="SMA", params={"timeperiod": 5}, operator="<", threshold=entry_threshold
            )
        ],
        exit_conditions=[
            PreviewCondition(
                indicator="SMA", params={"timeperiod": 5}, operator=">", threshold=999999.0
            )
        ],
        stop_loss_conditions=[
            PreviewCondition(indicator="SMA", params={"timeperiod": 5}, operator="<", threshold=0.0)
        ],
    )
    return json.loads(compiled.model_dump_json())


async def _create_execution(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    *,
    entry_threshold: float = 100.0,
    allocated_capital: Decimal = Decimal("1000"),
) -> int:
    strategy_id = f"tick-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps(_fsm_definition(entry_threshold=entry_threshold)),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', $3, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            allocated_capital,
        )
    return row["id"]


def _engines():
    return {
        "strategy_engine": StrategyEngine(),
        "portfolio_engine": PortfolioEngine(),
        "risk_engine": RiskEngine(load_risk_policy()),
        "executor": Executor(),
        "equity_tracker": ExecutionEquityTracker(),
        "policy": load_risk_policy(),
        "pre_submit_gate": _allow_gate,
        "distrust_monitor": DataDistrustMonitor(),
    }


async def _insert_terminal_order(
    pool: asyncpg.Pool,
    *,
    user_id: uuid.UUID,
    execution_id: int,
    strategy_id: str,
    strategy_version: str,
    status: str,
) -> None:
    await pool.execute(
        """
        INSERT INTO orders (
            order_id, user_id, client_order_id, exchange_order_id, strategy_id,
            strategy_version, execution_id, symbol, exchange, side, order_type,
            quantity, status, filled_quantity, is_liquidation, asset_class
        ) VALUES (
            gen_random_uuid(), $1, $2, $3, $4, $5,
            $6, 'BTC/USDT', 'bitget', 'BUY', 'MARKET', 0.01, $7, 0, false, 'CRYPTO'
        )
        """,
        user_id,
        f"{status.lower()}-{uuid.uuid4().hex}",
        f"ex-{status.lower()}-{uuid.uuid4().hex[:8]}",
        strategy_id,
        strategy_version,
        execution_id,
        status,
    )
