"""L4_strategy_portfolio_backtest_v1.0.md §9 L37 -- integration tests for
migration M3 (`627bd92ec750`) and the matching `contracts/v1.py`/
`ports/repository.py`/`adapters/postgres_repository.py` wiring: the
`ValidationRun`/`ValidationResult` artifact-linkage columns and the new
`strategy_validation_bundle` table + `PostgresValidationBundleRepository`.
Against a real dev DB, same convention as `test_run_check.py`. D2 evidence
(ADR-2026-09-09-C Decision 1): negative >= 3, failure-injection 1, numeric
performance assertion 1, gate-red repro 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.validation.adapters.postgres_repository import (
    PostgresValidationBundleRepository,
    PostgresValidationRepository,
)
from src.foundation.validation.domain.models import Outcome, ValidationResult
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
def bundle_repo(pool):
    return PostgresValidationBundleRepository(pool)


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
                condition="RSI_timeperiod14 < 0",
            ),
        ],
        author_agent="test",
    )


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


def _bundle_key() -> dict[str, str]:
    unique = uuid4().hex
    return {
        "artifact_hash": f"artifact-{unique}",
        "policy_version": "vp-v1",
        "data_snapshot_hash": f"snapshot-{unique}",
    }


# -- positive: artifact-linkage columns on strategy_validation_run ------------


async def test_create_run_round_trips_artifact_linkage_fields(
    pool, validation_repo, strategy_service
):
    owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)
    trace_id = str(uuid4())

    created = await validation_repo.create_run(
        strategy_id=strategy_id,
        strategy_version=version,
        check_type="backtest",
        input_snapshot_hash="l37-linkage-hash",
        cost_model={"fee_bps": "10", "slippage_bps": "5"},
        warmup_bars=5,
        periods_per_year=252,
        initial_equity=Decimal("1000"),
        artifact_hash="artifact-hash-l37",
        policy_version="vp-v2",
        seed=42,
        data_snapshot_hash="snapshot-hash-l37",
        trace_id=trace_id,
    )

    fetched = await validation_repo.get_run_by_snapshot(
        strategy_id, version, "backtest", "l37-linkage-hash"
    )
    assert fetched is not None
    assert fetched.artifact_hash == "artifact-hash-l37"
    assert fetched.policy_version == "vp-v2"
    assert fetched.seed == 42
    assert fetched.data_snapshot_hash == "snapshot-hash-l37"
    assert fetched.trace_id == trace_id
    assert created.id == fetched.id


async def test_create_run_defaults_artifact_linkage_fields_when_omitted(
    pool, validation_repo, strategy_service
):
    """Backward compatibility -- pre-L37 callers (`start_validation.py`) never
    pass these kwargs; the row must still land with the same defaults
    `domain/models.py`'s `ValidationRun` dataclass declares."""
    owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)

    await validation_repo.create_run(
        strategy_id=strategy_id,
        strategy_version=version,
        check_type="backtest",
        input_snapshot_hash="l37-defaults-hash",
        cost_model={"fee_bps": "10", "slippage_bps": "5"},
        warmup_bars=5,
        periods_per_year=252,
        initial_equity=Decimal("1000"),
    )

    fetched = await validation_repo.get_run_by_snapshot(
        strategy_id, version, "backtest", "l37-defaults-hash"
    )
    assert fetched is not None
    assert fetched.artifact_hash is None
    assert fetched.policy_version == "vp-v1"
    assert fetched.seed == 0
    assert fetched.data_snapshot_hash is None
    assert fetched.trace_id is None


# -- gate-red reproduction: evidence_refs persistence --------------------------


async def test_evidence_refs_survive_persistence_round_trip(
    pool, validation_repo, strategy_service
):
    """Red before this leaf: `complete_with_result`'s INSERT statement did not
    list an `evidence_refs` column at all (the column did not exist), so
    `run_check.py`'s `ValidationResult(..., evidence_refs=...)` was silently
    dropped on persistence -- `get_result_for_run` would return an empty
    tuple no matter what the caller supplied. Migration M3 adds the column
    and this leaf's INSERT/SELECT wiring closes that gap."""
    owner_id, strategy_id, version = await _saved_strategy(pool, strategy_service)
    run = await validation_repo.create_run(
        strategy_id=strategy_id,
        strategy_version=version,
        check_type="backtest",
        input_snapshot_hash="l37-evidence-refs-hash",
        cost_model={"fee_bps": "10", "slippage_bps": "5"},
        warmup_bars=5,
        periods_per_year=252,
        initial_equity=Decimal("1000"),
    )
    await validation_repo.mark_running(run.id)

    result = ValidationResult(
        id=uuid4(),
        run_id=run.id,
        outcome=Outcome.PASS,
        metrics={"sharpe_annualized": "1.2"},
        result_hash="deadbeef",
        evidence_refs=("snapshot:aaaa", "artifact:bbbb"),
    )
    _, saved = await validation_repo.complete_with_result(run.id, result)
    assert saved.evidence_refs == ("snapshot:aaaa", "artifact:bbbb")

    refetched = await validation_repo.get_result_for_run(run.id)
    assert refetched is not None
    assert refetched.evidence_refs == ("snapshot:aaaa", "artifact:bbbb")


# -- positive: strategy_validation_bundle --------------------------------------


async def test_create_bundle_and_get_bundle_round_trip(bundle_repo):
    key = _bundle_key()
    check_run_ids = (uuid4(), uuid4())

    created = await bundle_repo.create_bundle(
        **key,
        outcome=Outcome.PASS,
        check_run_ids=check_run_ids,
        bundle_hash="bundle-hash-1",
    )
    fetched = await bundle_repo.get_bundle(**key)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.outcome == Outcome.PASS
    assert set(fetched.check_run_ids) == set(check_run_ids)
    assert fetched.bundle_hash == "bundle-hash-1"
    assert fetched.hard_fail_reasons == ()


# -- negative (>=3) -------------------------------------------------------------


async def test_get_bundle_for_unknown_key_returns_none(bundle_repo):
    key = _bundle_key()
    assert await bundle_repo.get_bundle(**key) is None


async def test_create_bundle_duplicate_key_raises_concurrency_conflict(bundle_repo):
    key = _bundle_key()
    await bundle_repo.create_bundle(
        **key,
        outcome=Outcome.PASS,
        check_run_ids=(uuid4(),),
        bundle_hash="bundle-hash-first",
    )
    with pytest.raises(ConcurrencyConflictError):
        await bundle_repo.create_bundle(
            **key,
            outcome=Outcome.PASS,
            check_run_ids=(uuid4(),),
            bundle_hash="bundle-hash-second",
        )


async def test_raw_insert_violating_unique_constraint_is_rejected_at_db_layer(pool):
    """Same idempotency key as `test_create_bundle_duplicate_key_raises_
    concurrency_conflict`, but bypassing the repository entirely -- proves
    the UNIQUE constraint itself (not just the repo's exception mapping) is
    the actual backstop."""
    key = _bundle_key()
    insert_sql = (
        "INSERT INTO strategy_validation_bundle "
        "(artifact_hash, policy_version, data_snapshot_hash, outcome, "
        " check_run_ids, bundle_hash) "
        "VALUES ($1, $2, $3, 'PASS', $4, 'raw-hash')"
    )
    async with pool.acquire() as conn:
        await conn.execute(
            insert_sql,
            key["artifact_hash"],
            key["policy_version"],
            key["data_snapshot_hash"],
            [uuid4()],
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                insert_sql,
                key["artifact_hash"],
                key["policy_version"],
                key["data_snapshot_hash"],
                [uuid4()],
            )


# -- D2 failure injection: DB CHECK constraint (I6 forward direction) ---------


async def test_db_check_constraint_rejects_hard_fail_reasons_without_fail_outcome(pool):
    """Failure injection -- simulate a future writer (`build_bundle.py`, L43)
    that has a bug bypassing `ValidationBundle.__post_init__`'s I6 guard
    entirely (e.g. constructs the row from a raw dict) and tries to INSERT a
    PASS bundle that actually carries hard-fail reasons. Migration M3's
    `CHECK (cardinality(hard_fail_reasons) = 0 OR outcome = 'FAIL')` must
    reject it at the DB layer even though no Python code stood in the way."""
    key = _bundle_key()
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO strategy_validation_bundle "
                "(artifact_hash, policy_version, data_snapshot_hash, outcome, "
                " check_run_ids, bundle_hash, hard_fail_reasons) "
                "VALUES ($1, $2, $3, 'PASS', $4, 'raw-hash', $5)",
                key["artifact_hash"],
                key["policy_version"],
                key["data_snapshot_hash"],
                [uuid4()],
                ["INTEGRITY_FUTURE_DATA"],
            )
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM strategy_validation_bundle WHERE artifact_hash = $1",
            key["artifact_hash"],
        )
    assert count == 0


# -- D2 numeric performance assertion -------------------------------------------


async def test_p95_latency_within_bundle_round_trip_budget(bundle_repo):
    """ADR-2026-09-09-C axis performance budget: create_bundle + get_bundle
    against a real DB stays within a generous 200ms floor per iteration."""
    samples: list[float] = []
    for _ in range(5):
        key = _bundle_key()
        start = time.perf_counter()
        await bundle_repo.create_bundle(
            **key,
            outcome=Outcome.PASS,
            check_run_ids=(uuid4(),),
            bundle_hash="bundle-hash-perf",
        )
        await bundle_repo.get_bundle(**key)
        samples.append(time.perf_counter() - start)
    p95_seconds = max(samples)
    assert p95_seconds < 0.2, f"p95={p95_seconds * 1000:.2f}ms exceeds 200ms budget"
