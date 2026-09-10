"""R-46 risk_signal 통합테스트 -- 마이그레이션 d6f7b4c3e5a6 +
postgres_signal_repository.py + application/intraday_monitor.py.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#§9 R-46, §6 표 453행(dedupe),
§2 표 105/109행. DoD(a-e) 매핑: (a) insert_if_new 멱등 거부,
(b) dedupe_key floor(as_of,5min) 경계값 2점, (c) stale -> ACCOUNT PAUSE via
KillSwitchService, (d) 마이그레이션 왕복 + FK 거부 + 단일 head,
(e) 교차 테넌트 list_open 격리.

DEEPEN(task-2841, ADR-2026-09-09-C DEPTH audit) -- 이전 패스는 D2였다: FK
실패주입/마이그레이션 왕복/dedupe 경계는 이미 있었지만, 이 모듈의 핵심
안전장치인 `ON CONFLICT (dedupe_key) DO NOTHING`이 순차호출로만
검증됐고 실제 동시호출(병렬)에서는 한 번도 증명되지 않았다(D3 미달).
아래에 D2 체크리스트(negative>=3/실패주입 1/성능단언 1/게이트적색 재현 1)를
채우고, 그 핵심 갭인 다중 인스턴스 동시성 증명을 추가한다. 각 테스트는
어느 체크리스트 항목을 채우는지 주석으로 표시한다.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
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


# -- DEEPEN(task-2841) negative (>=3): risk_signal의 CHECK 제약이 애플리케이션
# 레이어를 우회해도(원시 SQL) 잘못된 값을 fail-closed로 거부한다. --------------


async def test_insert_with_invalid_type_violates_check_constraint(pool):
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO risk_signal "
                "(id, tenant_id, type, severity, dedupe_key, as_of, source) "
                "VALUES ($1, $2, 'NOT_A_TYPE', 'CRITICAL', $3, now(), 'test')",
                uuid4(),
                tenant_id,
                f"bad-type-{uuid4().hex}",
            )


async def test_insert_with_invalid_severity_violates_check_constraint(pool):
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO risk_signal "
                "(id, tenant_id, type, severity, dedupe_key, as_of, source) "
                "VALUES ($1, $2, 'DRAWDOWN', 'BADSEV', $3, now(), 'test')",
                uuid4(),
                tenant_id,
                f"bad-severity-{uuid4().hex}",
            )


async def test_insert_with_invalid_state_violates_check_constraint(pool):
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO risk_signal "
                "(id, tenant_id, type, severity, dedupe_key, as_of, source, state) "
                "VALUES ($1, $2, 'DRAWDOWN', 'CRITICAL', $3, now(), 'test', 'BADSTATE')",
                uuid4(),
                tenant_id,
                f"bad-state-{uuid4().hex}",
            )


async def test_recon_mismatch_breach_creates_signal_only_never_triggers_pause(
    pool, signal_repo, kill_switch_service
):
    """negative test -- §9 설계 결정: RECON_MISMATCH는 signal만 남기고 PAUSE를
    만들지 않는다(§6 473행이 이미 symbol-level DENY로 처리). `_PAUSE_SCOPE`에
    실수로 RECON_MISMATCH가 추가되는 회귀를 이 테스트가 잡는다."""
    tenant_id = await create_test_tenant(pool)
    metrics = _metrics(tenant_id, str(tenant_id), recon_mismatch=True)
    spy = mock.AsyncMock(wraps=kill_switch_service.activate)
    kill_switch_service.activate = spy
    repos = IntradayMonitorRepos(
        metrics=[metrics], signals=signal_repo, kill_switch=kill_switch_service
    )

    result = await run_intraday_monitor_once(repos, now=datetime.now(timezone.utc))

    assert len(result) == 1
    assert result[0].type == RiskSignalType.RECON_MISMATCH
    spy.assert_not_awaited()


# -- DEEPEN(task-2841) 실패 주입 (1): kill_switch.activate가 예외를 던져도
# 이미 커밋된 risk_signal 행은 롤백되지 않고, 나머지 계정 처리도 멈추지
# 않는다(모듈 docstring의 "best-effort" 설계 결정이 실제로 지켜지는지). ------


async def test_kill_switch_activation_failure_still_commits_signal_and_continues_next_account(
    pool, signal_repo, kill_switch_service
):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    metrics_a = _metrics(
        tenant_a,
        str(tenant_a),
        candle_age_seconds=Decimal("999"),
        staleness_limit_seconds=Decimal("120"),
    )
    metrics_b = _metrics(
        tenant_b,
        str(tenant_b),
        candle_age_seconds=Decimal("999"),
        staleness_limit_seconds=Decimal("120"),
    )
    real_activate = kill_switch_service.activate
    call_count = 0

    async def _flaky_activate(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated exchange outage during PAUSE activation")
        return await real_activate(**kwargs)

    kill_switch_service.activate = mock.AsyncMock(side_effect=_flaky_activate)
    repos = IntradayMonitorRepos(
        metrics=[metrics_a, metrics_b], signals=signal_repo, kill_switch=kill_switch_service
    )
    now = datetime.now(timezone.utc)

    result = await run_intraday_monitor_once(repos, now=now)

    assert len(result) == 2
    assert kill_switch_service.activate.await_count == 2
    async with pool.acquire() as conn:
        signal_count = await conn.fetchval(
            "SELECT count(*) FROM risk_signal WHERE tenant_id = ANY($1::uuid[])",
            [tenant_a, tenant_b],
        )
        control_b = await conn.fetchrow(
            "SELECT state FROM safety_control WHERE scope = 'ACCOUNT' AND scope_ref = $1",
            str(tenant_b),
        )
    assert signal_count == 2, "the first account's pause failure must not roll back its signal"
    assert control_b is not None
    assert control_b["state"] == "ACTIVE", "the second account's pause must still succeed"


# -- DEEPEN(task-2841) 성능 단언 (1): 순차 for 루프이므로 계정 수가 늘어도
# O(n) DB 왕복만 있어야 한다 -- 우발적 O(n^2)(예: 매 반복마다 list_open
# 전체 스캔을 추가하는 회귀) 방지. ------------------------------------------


async def test_many_accounts_completes_within_latency_budget(
    pool, signal_repo, kill_switch_service
):
    tenant_ids = [await create_test_tenant(pool) for _ in range(25)]
    metrics = [
        _metrics(
            tenant_id,
            str(tenant_id),
            equity_value=Decimal("500"),
            equity_peak=Decimal("1000"),
            drawdown_limit_pct=Decimal("10"),
        )
        for tenant_id in tenant_ids
    ]
    repos = IntradayMonitorRepos(
        metrics=metrics, signals=signal_repo, kill_switch=kill_switch_service
    )

    start = time.perf_counter()
    result = await run_intraday_monitor_once(repos, now=datetime.now(timezone.utc))
    elapsed = time.perf_counter() - start

    assert len(result) == 25
    assert elapsed < 5.0, f"25 accounts took {elapsed:.2f}s -- possible O(n^2) regression"


# -- DEEPEN(task-2841) 게이트 적색 재현 (1): 모듈 docstring의 설계 결정
# "PROVIDER_OUTAGE의 dedupe_key는 tenant_id 성분이 없어 같은 5분 창에서
# 여러 테넌트가 같은 공급자 장애를 관측해도 첫 insert_if_new만 이기고
# 나머지는 False를 본다"는 지금까지 한 번도 실행된 적 없는 주장이었다 --
# 실제로 재현해서 pause가 정확히 한 번만 발동함을 증명한다. --------------


async def test_provider_outage_same_window_only_first_tenant_triggers_pause(
    pool, signal_repo, kill_switch_service
):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    provider_code = f"outage-provider-{uuid4().hex}"
    metrics_a = _metrics(
        tenant_a, str(tenant_a), provider_code=provider_code, provider_available=False
    )
    metrics_b = _metrics(
        tenant_b, str(tenant_b), provider_code=provider_code, provider_available=False
    )
    spy = mock.AsyncMock(wraps=kill_switch_service.activate)
    kill_switch_service.activate = spy
    repos = IntradayMonitorRepos(
        metrics=[metrics_a, metrics_b], signals=signal_repo, kill_switch=kill_switch_service
    )
    now = datetime.now(timezone.utc)

    result = await run_intraday_monitor_once(repos, now=now)

    assert len(result) == 1, "second tenant's insert_if_new must see the dedupe as already recorded"
    spy.assert_awaited_once()
    assert spy.await_args.kwargs["scope_ref"] == provider_code
    async with pool.acquire() as conn:
        pause_count = await conn.fetchval(
            "SELECT count(*) FROM safety_control WHERE scope = 'PROVIDER' AND scope_ref = $1",
            provider_code,
        )
    assert pause_count == 1


# -- DEEPEN(task-2841) D3 다중 인스턴스 증명 (audit이 지적한 핵심 갭): 순차
# 호출이 아니라 실제 동시(asyncio.gather) 호출로 같은 dedupe_key를 놓고
# 경합시켜 `ON CONFLICT (dedupe_key) DO NOTHING`이 진짜 레이스 컨디션에서도
# 정확히 하나만 승리시키는지 증명한다. 순차 테스트(위의
# test_insert_if_new_second_call_...)는 이 레이스를 검증하지 못한다. -------


async def test_concurrent_insert_if_new_calls_with_same_dedupe_key_only_one_wins(pool, signal_repo):
    tenant_id = await create_test_tenant(pool)
    dedupe_key = f"DRAWDOWN:race-{uuid4().hex}:bucket"
    as_of = datetime.now(timezone.utc)

    async def _attempt() -> bool:
        return await signal_repo.insert_if_new(
            signal_id=uuid4(),
            dedupe_key=dedupe_key,
            tenant_id=tenant_id,
            signal_type="DRAWDOWN",
            severity="CRITICAL",
            as_of=as_of,
            source="test-concurrent",
        )

    results = await asyncio.gather(*(_attempt() for _ in range(16)))

    assert sum(1 for created in results if created) == 1, (
        "exactly one concurrent insert_if_new call must win the race"
    )
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM risk_signal WHERE dedupe_key = $1", dedupe_key
        )
    assert count == 1


async def test_concurrent_insert_if_new_different_pool_connections_only_one_wins(pool):
    """위 테스트가 asyncio 태스크 하나의 이벤트 루프 안에서만 경합했다면 이
    테스트는 각기 다른 `PostgresSignalRepository` 인스턴스(다른 워커
    프로세스를 흉내)가 같은 커넥션 풀 아래에서 동시에 경합해도 여전히
    하나만 이기는지 확인한다."""
    tenant_id = await create_test_tenant(pool)
    dedupe_key = f"STALE_DATA:race-instances-{uuid4().hex}:bucket"
    as_of = datetime.now(timezone.utc)
    repos = [PostgresSignalRepository(pool) for _ in range(8)]

    async def _attempt(repo: PostgresSignalRepository) -> bool:
        return await repo.insert_if_new(
            signal_id=uuid4(),
            dedupe_key=dedupe_key,
            tenant_id=tenant_id,
            signal_type="STALE_DATA",
            severity="CRITICAL",
            as_of=as_of,
            source="test-multi-instance",
        )

    results = await asyncio.gather(*(_attempt(repo) for repo in repos))

    assert sum(1 for created in results if created) == 1
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM risk_signal WHERE dedupe_key = $1", dedupe_key
        )
    assert count == 1
