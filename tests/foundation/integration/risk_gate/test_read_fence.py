"""R-33 read_fences/read_fence_snapshot 통합테스트 — 실 DB 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.4, §3.6.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.application.read_fence import read_fence_snapshot
from src.foundation.risk_gate.domain.fence import fence_pairs_for, is_stale
from src.foundation.risk_gate.domain.models import SafetyScope
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresRiskGateRepository(pool)


async def _tenant(pool):
    return await create_test_user(pool)


async def test_read_fences_defaults_never_activated_pairs_to_zero(pool, repo):
    """GLOBAL(scope_ref="")은 이 공유 테스트 DB에서 다른 테스트가 이미
    activate했을 수 있는 단조증가 카운터라 절대값 0을 보장할 수 없다
    (test_risk_gate_lifecycle.py 참조) — TENANT/ACCOUNT/PROVIDER/
    STRATEGY_DEPLOYMENT는 이 테스트가 새로 생성한 고유 참조라 0이 보장된다."""
    tenant_id = await _tenant(pool)
    unique_provider = f"never-activated-{uuid4().hex}"
    pairs = fence_pairs_for(tenant_id, unique_provider, str(uuid4()))

    snapshot = await repo.read_fences(pairs)

    assert set(snapshot.tokens.keys()) == set(pairs)
    non_global = [p for p in pairs if p[0] != SafetyScope.GLOBAL]
    assert all(snapshot.tokens[pair] == 0 for pair in non_global)


async def test_read_fences_reflects_only_the_activated_scope(pool, repo):
    tenant_id = await _tenant(pool)
    unique_provider = f"provider-{uuid4().hex}"
    pairs = fence_pairs_for(tenant_id, unique_provider, str(uuid4()))
    baseline = await repo.read_fences(pairs)

    control = await activate_safety_control(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="fence 조회 테스트",
    )

    snapshot = await repo.read_fences(pairs)

    account_pair = (SafetyScope.ACCOUNT, str(tenant_id))
    assert snapshot.tokens[account_pair] == control.fence_token
    for pair in pairs:
        if pair != account_pair:
            assert snapshot.tokens[pair] == baseline.tokens[pair]


async def test_read_fences_is_a_single_round_trip(pool, repo, monkeypatch):
    """R-33 DoD — 5쌍을 조회해도 물리 쿼리는 1회여야 한다(5회 왕복이면
    실패). `asyncpg.Connection.fetch`가 정확히 1번만 불렸는지로 단언한다."""
    tenant_id = await _tenant(pool)
    pairs = fence_pairs_for(tenant_id, "binance", str(uuid4()))

    call_count = 0
    original_fetch = asyncpg.Connection.fetch

    async def counting_fetch(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", counting_fetch)

    await repo.read_fences(pairs)

    assert call_count == 1


async def test_read_fence_snapshot_uses_the_fixed_five_pairs(pool, repo):
    tenant_id = await _tenant(pool)
    execution_ref = str(uuid4())

    snapshot = await read_fence_snapshot(
        repo, tenant_id=tenant_id, provider_code="binance", execution_ref=execution_ref
    )

    assert set(snapshot.tokens.keys()) == set(
        fence_pairs_for(tenant_id, "binance", execution_ref)
    )


async def test_is_stale_detects_activation_between_two_snapshots(pool, repo):
    """§3.6 F0/F1 비교 — 두 스냅샷 사이에 새 control이 activate되면
    is_stale이 True를 반환해야 한다(순수 함수 + 실 데이터 연동 확인)."""
    tenant_id = await _tenant(pool)
    execution_ref = str(uuid4())

    f0 = await read_fence_snapshot(
        repo, tenant_id=tenant_id, provider_code="binance", execution_ref=execution_ref
    )
    assert is_stale(f0, f0) is False

    await activate_safety_control(
        repo,
        tenant_id=tenant_id,
        actor_subject_id=tenant_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="stale 감지 테스트",
    )

    f1 = await read_fence_snapshot(
        repo, tenant_id=tenant_id, provider_code="binance", execution_ref=execution_ref
    )
    assert is_stale(f0, f1) is True
