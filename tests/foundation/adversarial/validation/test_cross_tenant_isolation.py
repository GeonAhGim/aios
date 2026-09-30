"""Strategy Validation adversarial 테스트 — 다른 소유자의 전략에 대해 검증을
시작할 수 없어야 한다(StrategyBuilderService.get_strategy()의 기존 소유자
검사를 그대로 통과시키는지 확인)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.validation.adapters.postgres_repository import PostgresValidationRepository
from src.foundation.validation.application.start_validation import (
    StrategyNotEligibleForValidationError,
    start_validation,
)
from src.foundation.validation.contracts.v1 import StartValidationCommand
from src.services.strategy_builder_service import (
    StrategyBuilderService,
    StrategyNotFoundError,
)
from tests.integration.conftest import create_test_user

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def validation_repo(pool: asyncpg.Pool) -> PostgresValidationRepository:
    return PostgresValidationRepository(pool)


@pytest.fixture
def strategy_service(pool: asyncpg.Pool) -> StrategyBuilderService:
    return StrategyBuilderService(pool)


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    def calculate(
        self, indicator: str, candles: list[Candle], **params: object
    ) -> _FakeIndicatorResult:  # noqa: ANN003
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _fsm_config() -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 10000",
            ),
        ],
        author_agent="test",
    )


def _bars() -> list[Candle]:
    return [
        Candle(
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
            open_time=_T0 + timedelta(hours=i),
            close_time=_T0 + timedelta(hours=i),
        )
        for i in range(3)
    ]


@pytest.mark.negative
async def test_cannot_start_validation_on_another_users_strategy(
    pool: asyncpg.Pool,
    validation_repo: PostgresValidationRepository,
    strategy_service: StrategyBuilderService,
) -> None:
    """불변식: 다른 사용자가 다른 사용자의 전략에 대해 검증을 시작할 수 없어야 한다
    (I-03, I-12 cross-tenant 격리)."""
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await strategy_service.save_strategy(
        owner_id,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=_fsm_config().model_dump(mode="json"),
    )
    await strategy_service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")

    command = StartValidationCommand(
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        cost_model_fee_bps=Decimal("5"),
        cost_model_slippage_bps=Decimal("2"),
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
    )

    with pytest.raises(StrategyNotEligibleForValidationError):
        await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=attacker_id,
            command=command,
            bars=_bars(),
            indicator_service=cast(Any, _FakePriceIndicatorService()),
        )

    # 공격 시도 이후에도 원 소유자의 전략은 그대로 BACKTESTING이어야 한다.
    detail = await strategy_service.get_strategy(owner_id, strategy_id, "1.0.0")
    assert detail.lifecycle_status == "BACKTESTING"


@pytest.mark.negative
async def test_get_strategy_denies_cross_tenant_read(
    pool: asyncpg.Pool, strategy_service: StrategyBuilderService
) -> None:
    """불변식: get_strategy()는 다른 사용자의 전략에 대해 '존재하지 않음'과 동일한
    예외를 던져야 한다 — 소유자가 아닌 요청에 lifecycle_status 등 어떤 필드도
    새어나가면 안 된다(I-03 cross-tenant 격리)."""
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await strategy_service.save_strategy(
        owner_id,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=_fsm_config().model_dump(mode="json"),
    )

    with pytest.raises(StrategyNotFoundError):
        await strategy_service.get_strategy(attacker_id, strategy_id, "1.0.0")


@pytest.mark.negative
async def test_cannot_start_validation_on_nonexistent_strategy(
    pool: asyncpg.Pool,
    validation_repo: PostgresValidationRepository,
    strategy_service: StrategyBuilderService,
) -> None:
    """불변식: 존재한 적 없는 strategy_id/version 조합으로도 검증을 시작할 수 없어야
    한다 — 소유자 검사가 '내 것이 아님'과 '아예 존재하지 않음'을 같은 실패
    경로로 막는지 확인한다(get_strategy가 두 경우 모두 StrategyNotFoundError를
    던지고, start_validation은 이를 StrategyNotEligibleForValidationError로
    포장한다)."""
    attacker_id = await create_test_user(pool)
    strategy_id = f"nonexistent-strategy-{uuid4().hex[:8]}"

    command = StartValidationCommand(
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        cost_model_fee_bps=Decimal("5"),
        cost_model_slippage_bps=Decimal("2"),
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
    )

    with pytest.raises(StrategyNotEligibleForValidationError):
        await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=attacker_id,
            command=command,
            bars=_bars(),
            indicator_service=cast(Any, _FakePriceIndicatorService()),
        )


@pytest.mark.failure_injection
async def test_start_validation_fails_closed_when_ownership_check_errors(
    monkeypatch: pytest.MonkeyPatch,
    pool: asyncpg.Pool,
    validation_repo: PostgresValidationRepository,
    strategy_service: StrategyBuilderService,
) -> None:
    """불변식: 소유자 검사(get_strategy) 도중 인프라 예외(DB 연결 끊김 등)가 나면
    그 예외를 삼키고 검증을 진행하는 대신 그대로 전파해야 한다 — 실패를
    삼키고 넘어가면 소유자 검사를 우회하는 fail-open이 되어버린다(105번
    §2.2 fail-closed 기본)."""
    owner_id = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await strategy_service.save_strategy(
        owner_id,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=_fsm_config().model_dump(mode="json"),
    )
    await strategy_service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")

    async def _boom(*args: object, **kwargs: object) -> None:
        raise ConnectionError("simulated DB outage during ownership check")

    monkeypatch.setattr(strategy_service, "get_strategy", _boom)

    create_run_called = False
    original_create_run = validation_repo.create_run

    async def _spy_create_run(*args: object, **kwargs: object) -> Any:
        nonlocal create_run_called
        create_run_called = True
        return await original_create_run(*args, **kwargs)

    monkeypatch.setattr(validation_repo, "create_run", _spy_create_run)

    command = StartValidationCommand(
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        cost_model_fee_bps=Decimal("5"),
        cost_model_slippage_bps=Decimal("2"),
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
    )

    with pytest.raises(ConnectionError):
        await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=owner_id,
            command=command,
            bars=_bars(),
            indicator_service=cast(Any, _FakePriceIndicatorService()),
        )

    assert create_run_called is False

    # 몽키패치되지 않은 별도 인스턴스로 확인 — 실패 이후에도 전략 상태와
    # 검증 실행 내역이 전혀 바뀌지 않았어야 한다.
    fresh_service = StrategyBuilderService(pool)
    detail = await fresh_service.get_strategy(owner_id, strategy_id, "1.0.0")
    assert detail.lifecycle_status == "BACKTESTING"


@pytest.mark.negative
async def test_empty_strategy_id_denied(
    pool: asyncpg.Pool, strategy_service: StrategyBuilderService
) -> None:
    """불변식: 빈 strategy_id로 전략을 조회할 수 없어야 한다(상한 입력 검증)."""
    user_id = await create_test_user(pool)

    with pytest.raises((StrategyNotFoundError, ValueError)):
        await strategy_service.get_strategy(user_id, "", "1.0.0")


@pytest.mark.failure_injection
async def test_validation_run_creation_failure_doesnt_leak_state(
    monkeypatch: pytest.MonkeyPatch,
    pool: asyncpg.Pool,
    validation_repo: PostgresValidationRepository,
    strategy_service: StrategyBuilderService,
) -> None:
    """불변식: validation_repo.create_run 중 예외가 발생하면 그 예외를 전파하고
    전략 상태를 변경하지 않아야 한다(fail-closed, 부분 업데이트 방지)."""
    owner_id = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await strategy_service.save_strategy(
        owner_id,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=_fsm_config().model_dump(mode="json"),
    )
    await strategy_service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated validation run creation failure")

    monkeypatch.setattr(validation_repo, "create_run", _boom)

    command = StartValidationCommand(
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        cost_model_fee_bps=Decimal("5"),
        cost_model_slippage_bps=Decimal("2"),
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
    )

    with pytest.raises(RuntimeError):
        await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=owner_id,
            command=command,
            bars=_bars(),
            indicator_service=cast(Any, _FakePriceIndicatorService()),
        )

    # 실패 이후에도 전략 상태는 BACKTESTING 유지
    fresh_service = StrategyBuilderService(pool)
    detail = await fresh_service.get_strategy(owner_id, strategy_id, "1.0.0")
    assert detail.lifecycle_status == "BACKTESTING"
