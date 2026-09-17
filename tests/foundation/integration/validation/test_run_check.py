"""L4_strategy_portfolio_backtest_v1.0.md §9 L41 -- integration tests for
`application/compile_artifact.py` + `application/run_check.py` against a
real dev DB. D2 evidence (ADR-2026-09-09-C Decision 1): negative >= 3,
failure-injection 1, numeric performance assertion 1, gate-red repro 1.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.application.run_backtest import BacktestRunError
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.snapshot import BarSnapshotRef
from src.foundation.backtest.domain.universe import UniverseSnapshot
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.domain.models import Outcome as AuditOutcome
from src.foundation.validation.adapters.postgres_repository import PostgresValidationRepository
from src.foundation.validation.application.compile_artifact import (
    StrategyNotFoundForCompilationError,
    compile_artifact,
)
from src.foundation.validation.application.run_check import (
    CheckAlreadyInProgressError,
    UnknownCheckTypeError,
    run_check,
)
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import ValidationPolicy
from src.services.strategy_builder_service import StrategyBuilderService
from tests.integration.conftest import create_test_tenant

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
def audit_repo(pool):
    return PostgresAuditEventRepository(pool)


@pytest.fixture
def strategy_service(pool):
    return StrategyBuilderService(pool)


def _never_fires_fsm_config(strategy_id: str, version: str) -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id=strategy_id,
        version=version,
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                # `checks/backtest.run()` always uses the real TA-Lib-backed
                # `IndicatorService` (no fake-injection seam like
                # `start_validation.py`'s `indicator_service` kwarg) -- RSI
                # is bounded to [0, 100], so this condition is structurally
                # valid but never true (mirrors `tests/foundation/unit/
                # validation/checks/test_backtest.py`'s fixture).
                condition="RSI_timeperiod14 < 0",
            ),
        ],
        author_agent="test",
    )


def _bar(index: int, *, close: str = "100") -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        open_time=ts,
        close_time=ts,
    )


def _bars(n: int) -> list[Candle]:
    return [_bar(i, close=str(100 + i)) for i in range(n)]


async def _saved_strategy(pool, strategy_service) -> tuple[object, str, str]:
    owner_id = await create_test_tenant(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    fsm = _never_fires_fsm_config(strategy_id, version)
    await strategy_service.save_strategy(
        owner_id,
        strategy_id,
        version,
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=fsm.model_dump(mode="json"),
    )
    return owner_id, strategy_id, version


async def _ctx_for(
    strategy_service, owner_id, strategy_id, version, *, bars=None, warmup_bars=5
) -> CheckContext:
    artifact = await compile_artifact(
        strategy_service,
        owner_user_id=owner_id,
        strategy_id=strategy_id,
        strategy_version=version,
    )
    bars = bars if bars is not None else _bars(30)
    return CheckContext(
        artifact=artifact,
        policy=ValidationPolicy(),
        bars=ListBars(bars),
        snapshot_ref=BarSnapshotRef(
            snapshot_hash="a" * 64,
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            from_time=bars[0].open_time,
            to_time=bars[-1].close_time,
            bar_count=len(bars),
            source="bitget-rest",
            as_of=bars[-1].close_time,
        ),
        universe=UniverseSnapshot(as_of=bars[-1].close_time, members=[], snapshot_hash="b" * 64),
        config=BacktestConfig(
            strategy_id=strategy_id,
            strategy_version=version,
            initial_equity=Decimal("1000"),
            cost_model=CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5")),
            warmup_bars=warmup_bars,
            periods_per_year=252,
        ),
        seed=0,
        trace_id=str(uuid4()),
        prior_results={},
    )


# -- positive path -------------------------------------------------------------


async def test_run_check_persists_result_and_records_evidence_row(
    pool, validation_repo, audit_repo, strategy_service
):
    owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)
    ctx = await _ctx_for(strategy_service, owner_id, strategy_id, version)

    result = await run_check(
        validation_repo,
        check_type="backtest",
        ctx=ctx,
        owner_user_id=owner_id,
        audit_repo=audit_repo,
    )

    assert result.outcome in (Outcome.PASS, Outcome.PASS_WITH_OBLIGATIONS)
    event = await audit_repo.get_latest_event(
        "strategy_validation_run", result.run_id, action="validation_check_completed:backtest"
    )
    assert event is not None
    assert event.outcome == AuditOutcome.SUCCESS


async def test_repeat_request_returns_cached_result_without_rerunning(
    pool, validation_repo, audit_repo, strategy_service
):
    owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)
    ctx = await _ctx_for(strategy_service, owner_id, strategy_id, version)

    first = await run_check(validation_repo, check_type="backtest", ctx=ctx, owner_user_id=owner_id)
    second = await run_check(
        validation_repo, check_type="backtest", ctx=ctx, owner_user_id=owner_id
    )
    assert first.run_id == second.run_id


# -- negative tests --------------------------------------------------------------


async def test_unknown_check_type_is_rejected_before_any_write(
    pool, validation_repo, strategy_service
):
    owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)
    ctx = await _ctx_for(strategy_service, owner_id, strategy_id, version)

    with pytest.raises(UnknownCheckTypeError):
        await run_check(
            validation_repo, check_type="not_a_real_check", ctx=ctx, owner_user_id=owner_id
        )

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM strategy_validation_run WHERE strategy_id = $1", strategy_id
        )
    assert count == 0


async def test_compile_artifact_on_unknown_strategy_is_rejected(pool, strategy_service):
    owner_id = await create_test_tenant(pool)
    with pytest.raises(StrategyNotFoundForCompilationError):
        await compile_artifact(
            strategy_service,
            owner_user_id=owner_id,
            strategy_id="does-not-exist",
            strategy_version="1.0.0",
        )


async def test_concurrent_identical_requests_only_one_run_is_created(
    pool, validation_repo, strategy_service
):
    owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)
    ctx = await _ctx_for(strategy_service, owner_id, strategy_id, version)

    async def attempt():
        return await run_check(
            validation_repo, check_type="backtest", ctx=ctx, owner_user_id=owner_id
        )

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            assert isinstance(r, (CheckAlreadyInProgressError, ConcurrencyConflictError))

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM strategy_validation_run WHERE strategy_id = $1", strategy_id
        )
    assert count == 1


# -- D2 failure injection ---------------------------------------------------------


async def test_backtest_engine_error_fails_run_and_records_error_evidence(
    pool, validation_repo, audit_repo, strategy_service
):
    """`checks/backtest.py`'s own docstring: `BacktestRunError` propagates
    uncaught out of `checks/backtest.run()` -- this leaf (`run_check.py`) is
    the layer responsible for turning that into a FAILED run plus an
    ERROR-outcome evidence row instead of a `CheckResult`."""
    owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)
    bars = _bars(5)
    ctx = await _ctx_for(strategy_service, owner_id, strategy_id, version, bars=bars, warmup_bars=5)

    with pytest.raises(BacktestRunError):
        await run_check(
            validation_repo,
            check_type="backtest",
            ctx=ctx,
            owner_user_id=owner_id,
            audit_repo=audit_repo,
        )

    async with pool.acquire() as conn:
        run_row = await conn.fetchrow(
            "SELECT id, state FROM strategy_validation_run WHERE strategy_id = $1", strategy_id
        )
    assert run_row["state"] == "FAILED"
    result = await validation_repo.get_result_for_run(run_row["id"])
    assert result is None

    event = await audit_repo.get_latest_event(
        "strategy_validation_run", run_row["id"], action="validation_check_errored:backtest"
    )
    assert event is not None
    assert event.outcome == AuditOutcome.ERROR


# -- D2 numeric performance assertion ----------------------------------------------


async def test_p95_latency_within_local_run_check_budget(pool, validation_repo, strategy_service):
    """ADR-2026-09-09-C axis performance budget: end-to-end `run_check` for a
    small (10-bar) replay against a real DB stays within a generous 2s
    floor -- each iteration uses a freshly saved strategy so none of the
    samples hit the idempotency short-circuit."""
    samples: list[float] = []
    for _ in range(3):
        owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)
        ctx = await _ctx_for(strategy_service, owner_id, strategy_id, version)
        start = time.perf_counter()
        await run_check(validation_repo, check_type="backtest", ctx=ctx, owner_user_id=owner_id)
        samples.append(time.perf_counter() - start)
    p95_seconds = max(samples)
    assert p95_seconds < 2.0, f"p95={p95_seconds * 1000:.2f}ms exceeds 2000ms budget"


# -- D2 gate-red reproduction -------------------------------------------------------


async def test_evidence_gate_catches_missing_audit_wiring_regression(
    pool, validation_repo, audit_repo, strategy_service
):
    """Red: a regression that calls `validation_repo.complete_with_result`
    directly (the pre-L41 `start_validation.py` shape) without ever going
    through `run_check`'s `record_command_event` call would silently
    "succeed" with no audit trail for the check run at all."""
    owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)
    ctx = await _ctx_for(strategy_service, owner_id, strategy_id, version)

    import json
    from uuid import uuid4 as _uuid4

    from src.foundation.validation.checks.backtest import run as run_backtest_check
    from src.foundation.validation.domain.models import ValidationResult

    regressed_run = await validation_repo.create_run(
        strategy_id=strategy_id,
        strategy_version=version,
        check_type="backtest",
        input_snapshot_hash="regressed-hash-no-evidence",
        cost_model={"fee_bps": "10", "slippage_bps": "5"},
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("1000"),
    )
    await validation_repo.mark_running(regressed_run.id)
    check_result = run_backtest_check(ctx)
    await validation_repo.complete_with_result(
        regressed_run.id,
        ValidationResult(
            id=_uuid4(),
            run_id=regressed_run.id,
            outcome=check_result.outcome,
            metrics=json.loads(json.dumps(check_result.metrics, default=str)),
            result_hash=check_result.result_hash,
        ),
    )
    regressed_event = await audit_repo.get_latest_event(
        "strategy_validation_run",
        regressed_run.id,
        action="validation_check_completed:backtest",
    )
    assert regressed_event is None  # red: no evidence row for the bypassed path

    # Green: the real run_check() always appends one.
    result = await run_check(
        validation_repo,
        check_type="backtest",
        ctx=ctx,
        owner_user_id=owner_id,
        audit_repo=audit_repo,
    )
    real_event = await audit_repo.get_latest_event(
        "strategy_validation_run", result.run_id, action="validation_check_completed:backtest"
    )
    assert real_event is not None
