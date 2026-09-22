"""L44 통합테스트 — 실제 dev DB 대상. spec §9 L44 DoD "result_hash 상수 대조
통과"(STR-001 "same source/config/input/seed compiles and validates to same
... result hash", spec line 605 "test_reproducibility.py의 기대 해시 상수는
L44에서 첫 실행값을 박고, 이후 변경은 커밋 메시지에
`reproducibility-hash-change:` 접두어와 사유를 요구한다").

L43(application/build_bundle.py, projections.py, 라우터 — 6개 체크를 묶어
bundle_hash를 내는 파이프라인)은 여전히 미구현이다(src/foundation/validation/
application/에 build_bundle.py·projections.py가 없고, ports/repository.py:77·
contracts/v1.py:72 주석도 "아직 없는 later leaf"로 명시). 이 리프가 검증할 수
있는 재현성은 지금 실제로 배선된 단일-체크(backtest, L41+L42) 파이프라인이
내는 `ValidationResult.result_hash`뿐이다 — `domain/rules.compute_result_hash`가
그 상수 산식이고, `application/start_validation.py`가 그 값을 실제로 DB에
써서 반환하는 유일한 경로다.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.validation.adapters.postgres_repository import PostgresValidationRepository
from src.foundation.validation.application import start_validation as start_validation_mod
from src.foundation.validation.application.start_validation import start_validation
from src.foundation.validation.contracts.v1 import Outcome, StartValidationCommand
from src.services.strategy_builder_service import StrategyBuilderService
from tests.integration.conftest import create_test_user

# ADR-2026-09-09-C Decision 1 예산 -- "백테스트 1개월 M1 1심볼 3초". 검증
# 실행이 내부적으로 이 백테스트 엔진을 그대로 호출하므로 같은 예산을 쓴다.
_BACKTEST_BUDGET_S = 3.0

_T0 = datetime.fromisoformat("2026-01-01T00:00:00+00:00")


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def validation_repo(pool):
    return PostgresValidationRepository(pool)


@pytest.fixture
def strategy_service(pool):
    return StrategyBuilderService(pool)


class _FakePriceIndicatorService:
    """TA-Lib 의존 없이 오케스트레이션만 검증(test_start_validation.py와 동일)."""

    def calculate(self, indicator: str, candles: list[Candle], **params: int):
        assert indicator == "PRICE"
        return type("R", (), {"values": [float(candles[-1].close)]})()


def _never_fires_fsm_config() -> FSMStrategyConfig:
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


def _single_trade_fsm_config() -> FSMStrategyConfig:
    """항상 첫 봉에서 진입해 그대로 들고 있는(재진입 없는) FSM -- 실제로
    체결이 생겨야 cost model/initial equity/bars 값이 metrics(따라서
    result_hash)에 반영된다. `_never_fires_fsm_config`는 거래가 전혀 없어
    이 세 입력이 metrics에 아무 영향도 주지 않는다."""
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING, FSMState.HOLDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 0",
            ),
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.HOLDING,
                condition="ORDER_FILLED",
            ),
        ],
        author_agent="test",
    )


def _re_entry_bug_fsm_config() -> FSMStrategyConfig:
    """test_start_validation.py의 F-04 회귀 픽스처와 동일 — 항상
    hard_fail_reasons가 채워지는 outcome=FAIL 경로를 재현성 관점에서 쓴다."""
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING, FSMState.HOLDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 0",
            ),
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.HOLDING,
                condition="ORDER_FILLED",
            ),
            FSMTransition(
                from_state=FSMState.HOLDING,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 0",
            ),
        ],
        author_agent="test",
    )


def _bars(count: int = 5, close: str = "100") -> list[Candle]:
    return [
        Candle(
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("1"),
            open_time=_T0 + timedelta(hours=i),
            close_time=_T0 + timedelta(hours=i),
        )
        for i in range(count)
    ]


def _trending_bars(count: int = 5, start: int = 100, step: int = 0) -> list[Candle]:
    """`_bars`는 항상 평평해 보유 포지션의 미실현 손익이 0으로 남는다 --
    `_single_trade_fsm_config`가 진입 후 계속 HOLDING이므로, 봉 시퀀스가
    다르다는 걸 result_hash에 실제로 반영시키려면 가격이 움직여야 한다."""
    return [
        Candle(
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            open=Decimal(start + step * i),
            high=Decimal(start + step * i),
            low=Decimal(start + step * i),
            close=Decimal(start + step * i),
            volume=Decimal("1"),
            open_time=_T0 + timedelta(hours=i),
            close_time=_T0 + timedelta(hours=i),
        )
        for i in range(count)
    ]


async def _strategy_in_backtesting(
    pool, strategy_service, fsm_config: FSMStrategyConfig | None = None
) -> tuple[UUID, str, str]:
    owner_id = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    fsm_definition = (fsm_config or _never_fires_fsm_config()).model_dump(mode="json")
    await strategy_service.save_strategy(
        owner_id,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=fsm_definition,
    )
    await strategy_service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")
    return owner_id, strategy_id, "1.0.0"


def _command(strategy_id: str, version: str, **overrides) -> StartValidationCommand:
    defaults = dict(
        strategy_id=strategy_id,
        strategy_version=version,
        cost_model_fee_bps=Decimal("5"),
        cost_model_slippage_bps=Decimal("2"),
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
    )
    defaults.update(overrides)
    return StartValidationCommand(**defaults)


async def _run(pool, validation_repo, strategy_service, *, fsm_config=None, bars=None, **overrides):
    owner_id, strategy_id, version = await _strategy_in_backtesting(
        pool, strategy_service, fsm_config=fsm_config
    )
    view = await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=owner_id,
        command=_command(strategy_id, version, **overrides),
        bars=bars if bars is not None else _bars(),
        indicator_service=_FakePriceIndicatorService(),
    )
    return view


# ---- 상수 대조(pinned hash) ----

# spec 605행: 첫 실행값을 박는다. 아래 입력(고정 FSM/bars/cost model)으로
# start_validation()을 실행하면 evaluate_validation_policy가
# PASS_WITH_OBLIGATIONS를 반환하고(0 fee/slippage가 아니므로 obligation
# 없음 -> PASS), compute_result_hash(metrics)가 이 상수를 낸다. 이 값이
# 바뀌면 산식/픽스처가 바뀐 것 -- 커밋 메시지에
# `reproducibility-hash-change:` 접두어 + 사유가 필요하다(spec line 605).
_PINNED_RESULT_HASH = "03caa3957fe7eb96bedf9d38085e58a43a06657e7ee44024c4ef86478cf53c8a"


async def test_result_hash_matches_pinned_constant_for_fixed_inputs(
    pool, validation_repo, strategy_service
):
    view = await _run(pool, validation_repo, strategy_service)

    assert view.outcome == Outcome.PASS
    assert view.result_hash == _PINNED_RESULT_HASH


async def test_two_independent_runs_with_identical_inputs_yield_identical_hash(
    pool, validation_repo, strategy_service
):
    """멱등성 캐시(같은 run_id 반환)를 우회하려고 서로 다른 전략에 대해
    두 번 실행한다 -- 산식 자체가 결정적임을(캐시 재사용이 아니라) 증명."""
    first = await _run(pool, validation_repo, strategy_service)
    second = await _run(pool, validation_repo, strategy_service)

    assert first.run_id != second.run_id
    assert first.result_hash == second.result_hash


# ---- negative: 입력이 하나라도 다르면 해시도 달라져야 한다 (>=3) ----


async def test_different_bars_change_result_hash(pool, validation_repo, strategy_service):
    fsm_config = _single_trade_fsm_config()
    baseline = await _run(
        pool, validation_repo, strategy_service, fsm_config=fsm_config, bars=_trending_bars(step=0)
    )
    changed = await _run(
        pool, validation_repo, strategy_service, fsm_config=fsm_config, bars=_trending_bars(step=5)
    )

    assert baseline.result_hash != changed.result_hash


async def test_different_cost_model_changes_result_hash(pool, validation_repo, strategy_service):
    fsm_config = _single_trade_fsm_config()
    baseline = await _run(pool, validation_repo, strategy_service, fsm_config=fsm_config)
    changed = await _run(
        pool,
        validation_repo,
        strategy_service,
        fsm_config=fsm_config,
        cost_model_fee_bps=Decimal("50"),
    )

    assert baseline.result_hash != changed.result_hash


async def test_different_initial_equity_changes_result_hash(
    pool, validation_repo, strategy_service
):
    fsm_config = _single_trade_fsm_config()
    baseline = await _run(pool, validation_repo, strategy_service, fsm_config=fsm_config)
    changed = await _run(
        pool,
        validation_repo,
        strategy_service,
        fsm_config=fsm_config,
        initial_equity=Decimal("50000"),
    )

    assert baseline.result_hash != changed.result_hash


# ---- 실패 주입 ----


async def test_result_hash_reproducible_across_fail_outcome_too(
    pool, validation_repo, strategy_service
):
    """실패 주입: outcome=FAIL(엔진 재생 오류로 hard_fail_reasons가 채워지는
    경로, test_start_validation.py의 F-04 회귀와 동일 픽스처)에서도
    result_hash 재현성이 유지되는지 -- PASS 경로만 결정적이고 FAIL 경로는
    타이밍에 따라 흔들리는 회귀를 잡는다."""
    fsm_config = _re_entry_bug_fsm_config()
    bars = _bars(count=4)
    first = await _run(pool, validation_repo, strategy_service, fsm_config=fsm_config, bars=bars)
    second = await _run(pool, validation_repo, strategy_service, fsm_config=fsm_config, bars=bars)

    assert first.outcome == Outcome.FAIL
    assert second.outcome == Outcome.FAIL
    assert first.result_hash == second.result_hash


# ---- 성능 단언 ----


async def test_validation_run_stays_within_backtest_budget(pool, validation_repo, strategy_service):
    """ADR-2026-09-09-C Decision 1 -- 백테스트 1개월 M1 1심볼 3초 예산.
    start_validation()이 내부에서 그 백테스트 엔진을 호출하므로 같은
    예산을 단언한다."""
    owner_id, strategy_id, version = await _strategy_in_backtesting(pool, strategy_service)

    started = time.perf_counter()
    await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=owner_id,
        command=_command(strategy_id, version),
        bars=_bars(),
        indicator_service=_FakePriceIndicatorService(),
    )
    elapsed_s = time.perf_counter() - started

    assert elapsed_s < _BACKTEST_BUDGET_S


# ---- 게이트 적색 재현 ----


async def test_budget_gate_actually_fails_when_result_hash_computation_stalls(
    pool, validation_repo, strategy_service
):
    """게이트 적색 재현: `compute_result_hash`가 예산(3초)을 실제로 넘기도록
    지연을 주입하면, 위 성능 단언과 동일한 식이 실제로 AssertionError를
    내는지 확인한다 -- 그래야 위 단언이 항상 통과하는 tautology가 아님을
    보장한다."""
    owner_id, strategy_id, version = await _strategy_in_backtesting(pool, strategy_service)

    original = start_validation_mod.compute_result_hash

    def _stalled(metrics):
        time.sleep(_BACKTEST_BUDGET_S + 0.1)
        return original(metrics)

    started = time.perf_counter()
    with patch.object(start_validation_mod, "compute_result_hash", _stalled):
        await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=owner_id,
            command=_command(strategy_id, version),
            bars=_bars(),
            indicator_service=_FakePriceIndicatorService(),
        )
    elapsed_s = time.perf_counter() - started

    with pytest.raises(AssertionError):
        assert elapsed_s < _BACKTEST_BUDGET_S
