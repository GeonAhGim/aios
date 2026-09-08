"""R-46 risk_signal 통합테스트 -- 마이그레이션 d6f7b4c3e5a6 +
postgres_signal_repository.py + application/intraday_monitor.py.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#§9 R-46, §6 표 453행(dedupe),
§2 표 105/109행. DoD(a-e) 매핑: (a) insert_if_new 멱등 거부,
(b) dedupe_key floor(as_of,5min) 경계값 2점, (c) stale -> ACCOUNT PAUSE via
KillSwitchService, (d) 마이그레이션 왕복 + FK 거부 + 단일 head,
(e) 교차 테넌트 list_open 격리.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.adapters.postgres_signal_repository import (
    PostgresSignalRepository,
    dedupe_key_for,
)
from src.foundation.risk_gate.application.intraday_monitor import (
    AccountMetrics,
    IntradayMonitorRepos,
    run_intraday_monitor_once,
)
from src.foundation.risk_gate.domain.models import RiskSignalType, SafetyScope
from src.services.safety.kill_switch_service import KillSwitchService
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DOWN_REVISION = "b76f4590b1b8"


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )
    return result


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def signal_repo(pool):
    return PostgresSignalRepository(pool)


@pytest.fixture
def kill_switch_service(pool):
    return KillSwitchService(
        risk_gate_repo=PostgresRiskGateRepository(pool),
        pg_pool=pool,
        paper_control_repo=PostgresPaperControlRepository(pool),
        exchange_adapters={"bitget": FakeExchangeAdapter(exchange_name="bitget")},
        audit_repo=PostgresAuditEventRepository(pool),
    )


@pytest.fixture(autouse=True)
def _ensure_head():
    _run_alembic("upgrade", "head")
    yield
    _run_alembic("upgrade", "head")


async def _table_exists(pool: asyncpg.Pool, table_name: str) -> bool:
    async with pool.acquire() as conn:
        reg = await conn.fetchval("SELECT to_regclass($1)", f"public.{table_name}")
    return reg is not None


def _metrics(tenant_id, account_ref: str, **overrides) -> AccountMetrics:
    base = dict(
        tenant_id=tenant_id,
        account_ref=account_ref,
        provider_code="bitget",
        equity_value=Decimal("1000"),
        equity_peak=Decimal("1000"),
        drawdown_limit_pct=Decimal("100"),
        candle_age_seconds=None,
        staleness_limit_seconds=Decimal("120"),
        provider_available=True,
        recon_mismatch=False,
    )
    base.update(overrides)
    return AccountMetrics(**base)


# -- (d) 마이그레이션 왕복 + 단일 head + FK 거부 ----------------------------


def test_alembic_heads_is_single():
    result = _run_alembic("heads")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"alembic heads가 단일이어야 합니다: {result.stdout}"


async def test_upgrade_downgrade_upgrade_round_trip_recreates_risk_signal(pool):
    assert await _table_exists(pool, "risk_signal")

    _run_alembic("downgrade", _DOWN_REVISION)
    assert not await _table_exists(pool, "risk_signal")

    _run_alembic("upgrade", "head")
    assert await _table_exists(pool, "risk_signal")


async def test_insert_with_nonexistent_tenant_id_raises_fk_violation(pool):
    missing_tenant_id = uuid4()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO risk_signal "
                "(id, tenant_id, type, severity, dedupe_key, as_of, source) "
                "VALUES ($1, $2, 'DRAWDOWN', 'CRITICAL', $3, now(), 'test')",
                uuid4(),
                missing_tenant_id,
                f"orphan-{uuid4().hex}",
            )


# -- (a)(b) insert_if_new 멱등 + dedupe_key 5분 경계 -----------------------


async def test_insert_if_new_second_call_with_same_dedupe_key_returns_false(pool, signal_repo):
    tenant_id = await create_test_tenant(pool)
    dedupe_key = f"DRAWDOWN:acct-{uuid4().hex}:bucket"
    as_of = datetime.now(timezone.utc)

    first = await signal_repo.insert_if_new(
        signal_id=uuid4(),
        dedupe_key=dedupe_key,
        tenant_id=tenant_id,
        signal_type="DRAWDOWN",
        severity="CRITICAL",
        as_of=as_of,
        source="test",
    )
    second = await signal_repo.insert_if_new(
        signal_id=uuid4(),
        dedupe_key=dedupe_key,
        tenant_id=tenant_id,
        signal_type="DRAWDOWN",
        severity="CRITICAL",
        as_of=as_of,
        source="test",
    )

    assert first is True
    assert second is False
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM risk_signal WHERE dedupe_key = $1", dedupe_key
        )
    assert count == 1


async def test_dedupe_key_boundary_04_59_59_and_05_00_00_are_different_keys(pool, signal_repo):
    tenant_id = await create_test_tenant(pool)
    scope_ref = f"acct-{uuid4().hex}"
    before = datetime(2026, 1, 1, 4, 59, 59, tzinfo=timezone.utc)
    at_boundary = datetime(2026, 1, 1, 5, 0, 0, tzinfo=timezone.utc)

    key_before = dedupe_key_for("STALE_DATA", scope_ref, before)
    key_at = dedupe_key_for("STALE_DATA", scope_ref, at_boundary)
    assert key_before != key_at

    for as_of, key in ((before, key_before), (at_boundary, key_at)):
        created = await signal_repo.insert_if_new(
            signal_id=uuid4(),
            dedupe_key=key,
            tenant_id=tenant_id,
            signal_type="STALE_DATA",
            severity="CRITICAL",
            as_of=as_of,
            source="test",
        )
        assert created is True

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM risk_signal WHERE tenant_id = $1", tenant_id
        )
    assert count == 2


async def test_dedupe_key_boundary_05_00_00_and_05_04_59_are_same_key(pool, signal_repo):
    tenant_id = await create_test_tenant(pool)
    scope_ref = f"acct-{uuid4().hex}"
    at_boundary = datetime(2026, 1, 1, 5, 0, 0, tzinfo=timezone.utc)
    just_before_next = datetime(2026, 1, 1, 5, 4, 59, tzinfo=timezone.utc)

    key_at = dedupe_key_for("STALE_DATA", scope_ref, at_boundary)
    key_late = dedupe_key_for("STALE_DATA", scope_ref, just_before_next)
    assert key_at == key_late

    first = await signal_repo.insert_if_new(
        signal_id=uuid4(),
        dedupe_key=key_at,
        tenant_id=tenant_id,
        signal_type="STALE_DATA",
        severity="CRITICAL",
        as_of=at_boundary,
        source="test",
    )
    second = await signal_repo.insert_if_new(
        signal_id=uuid4(),
        dedupe_key=key_late,
        tenant_id=tenant_id,
        signal_type="STALE_DATA",
        severity="CRITICAL",
        as_of=just_before_next,
        source="test",
    )
    assert first is True
    assert second is False

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM risk_signal WHERE tenant_id = $1", tenant_id
        )
    assert count == 1


# -- (e) 교차 테넌트 list_open ----------------------------------------------


async def test_list_open_returns_zero_rows_for_other_tenant(pool, signal_repo):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await signal_repo.insert_if_new(
        signal_id=uuid4(),
        dedupe_key=f"DRAWDOWN:a-{uuid4().hex}:x",
        tenant_id=tenant_a,
        signal_type="DRAWDOWN",
        severity="CRITICAL",
        as_of=datetime.now(timezone.utc),
        source="test",
    )

    open_for_b = await signal_repo.list_open(tenant_b)

    assert open_for_b == ()


async def test_list_open_returns_only_this_tenants_signals(pool, signal_repo):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await signal_repo.insert_if_new(
        signal_id=uuid4(),
        dedupe_key=f"DRAWDOWN:a-{uuid4().hex}:x",
        tenant_id=tenant_a,
        signal_type="DRAWDOWN",
        severity="CRITICAL",
        as_of=datetime.now(timezone.utc),
        source="test",
    )
    await signal_repo.insert_if_new(
        signal_id=uuid4(),
        dedupe_key=f"DRAWDOWN:b-{uuid4().hex}:x",
        tenant_id=tenant_b,
        signal_type="DRAWDOWN",
        severity="CRITICAL",
        as_of=datetime.now(timezone.utc),
        source="test",
    )

    open_for_a = await signal_repo.list_open(tenant_a)

    assert len(open_for_a) == 1
    assert open_for_a[0].tenant_id == tenant_a


# -- (c) stale -> ACCOUNT PAUSE via KillSwitchService ----------------------


async def test_stale_candle_creates_signal_and_account_pause_via_kill_switch(
    pool, signal_repo, kill_switch_service
):
    tenant_id = await create_test_tenant(pool)
    account_ref = str(tenant_id)
    metrics = _metrics(
        tenant_id,
        account_ref,
        candle_age_seconds=Decimal("999"),
        staleness_limit_seconds=Decimal("120"),
    )
    spy = mock.AsyncMock(wraps=kill_switch_service.activate)
    kill_switch_service.activate = spy
    repos = IntradayMonitorRepos(
        metrics=[metrics], signals=signal_repo, kill_switch=kill_switch_service
    )
    now = datetime.now(timezone.utc)

    result = await run_intraday_monitor_once(repos, now=now)

    assert len(result) == 1
    assert result[0].type == RiskSignalType.STALE_DATA
    spy.assert_awaited_once()
    assert spy.await_args.kwargs["scope"] == SafetyScope.ACCOUNT
    assert spy.await_args.kwargs["scope_ref"] == account_ref

    async with pool.acquire() as conn:
        signal_count = await conn.fetchval(
            "SELECT count(*) FROM risk_signal WHERE tenant_id = $1 AND type = 'STALE_DATA'",
            tenant_id,
        )
        control = await conn.fetchrow(
            "SELECT scope, scope_ref, state, fence_token FROM safety_control "
            "WHERE scope = 'ACCOUNT' AND scope_ref = $1",
            account_ref,
        )
    assert signal_count == 1
    assert control is not None
    assert control["state"] == "ACTIVE"
    assert control["fence_token"] >= 1


async def test_stale_candle_second_pass_same_window_does_not_pause_again(
    pool, signal_repo, kill_switch_service
):
    """RSK-008 dedupe -- 같은 5분 창 안에서 다시 관측돼도 signal도 pause도
    중복되지 않는다(멱등)."""
    tenant_id = await create_test_tenant(pool)
    account_ref = str(tenant_id)
    metrics = _metrics(
        tenant_id,
        account_ref,
        candle_age_seconds=Decimal("999"),
        staleness_limit_seconds=Decimal("120"),
    )
    spy = mock.AsyncMock(wraps=kill_switch_service.activate)
    kill_switch_service.activate = spy
    repos = IntradayMonitorRepos(
        metrics=[metrics], signals=signal_repo, kill_switch=kill_switch_service
    )
    now = datetime(2026, 1, 1, 5, 1, 0, tzinfo=timezone.utc)
    same_window_later = datetime(2026, 1, 1, 5, 4, 0, tzinfo=timezone.utc)

    first = await run_intraday_monitor_once(repos, now=now)
    second = await run_intraday_monitor_once(repos, now=same_window_later)

    assert len(first) == 1
    assert len(second) == 0
    spy.assert_awaited_once()


async def test_no_breach_emits_no_signal(pool, signal_repo, kill_switch_service):
    """negative test -- 임계값을 넘지 않으면 아무 신호도 만들지 않는다."""
    tenant_id = await create_test_tenant(pool)
    metrics = _metrics(tenant_id, str(tenant_id))

    repos = IntradayMonitorRepos(
        metrics=[metrics], signals=signal_repo, kill_switch=kill_switch_service
    )

    result = await run_intraday_monitor_once(repos, now=datetime.now(timezone.utc))

    assert result == []
