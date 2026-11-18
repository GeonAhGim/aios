"""AI-4 성능 테스트 -- authorize() DB 왕복 p95 단언 + 지연 주입 적색 검증.

성능 테스트는 test_token_lifecycle.py에서 분리(LOC 분할, task-18519).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.authorize import authorize
from src.foundation.ai.gateway.application.issue_token import issue_token
from src.foundation.ai.gateway.domain.token_rules import Scope
from tests.support.perf_inject import virtual_delayed_async

_AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS = 50.0

_NOW = datetime.now(UTC)


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresAgentTokenRepository:
    return PostgresAgentTokenRepository(pool)


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.perf
async def test_authorize_db_roundtrip_p95_within_budget(
    repo: PostgresAgentTokenRepository, perf_budget
):
    """RelativeBudget.measure_async → perf_budget.fixture 전환(task-25740).

    DB 왕복은 I/O 바운드이므로 wall_ms(벽시계)로 측정한다. PerfBudget.sample_async는
    time.process_time() + time.perf_counter()를 같이 돌려 I/O 지연을 wall_ms로
    정확히 포착한다.
    """
    tenant_id = uuid4()
    issued = await issue_token(
        repo,
        tenant_id=tenant_id,
        scopes=frozenset({Scope.READ}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        ttl=timedelta(hours=1),
        now=_NOW,
    )

    samples = await perf_budget.samples_async(
        lambda: authorize(repo, token_secret=issued.secret, scope=Scope.READ),
        n=30,
        batch=1,
    )
    wall_ms_samples = [s.wall_ms for s in samples]

    p95_ms = _p95(wall_ms_samples)
    print(
        f"[AI-4 authorize] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS


@pytest.mark.perf
async def test_authorize_p95_detects_tenfold_delay_injection(
    repo: PostgresAgentTokenRepository,
    perf_budget,
    monkeypatch: pytest.MonkeyPatch,
):
    """성능 테스트가 실제 지연을 포착하는지 검증 — authorize() 내부의 DB
    왕복에 5초 지연을 주입하면 p95가 50ms 예산을 10배 넘어서 테스트가
    적색이어야 한다.

    이 테스트가 통과하면 기존 녹색 단언이 tautology가 아님을 확인한다.
    """
    tenant_id = uuid4()
    issued = await issue_token(
        repo,
        tenant_id=tenant_id,
        scopes=frozenset({Scope.READ}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        ttl=timedelta(hours=1),
        now=_NOW,
    )

    # repo.get_by_hash()에 5초 지연 주입 — virtual_delayed_async로 측정치만 느리게 (task-20689)
    monkeypatch.setattr(repo, "get_by_hash", virtual_delayed_async(repo.get_by_hash, 5.0))

    samples = await perf_budget.samples_async(
        lambda: authorize(repo, token_secret=issued.secret, scope=Scope.READ),
        n=5,
        batch=1,
    )
    wall_ms_samples = [s.wall_ms for s in samples]

    p95_ms = _p95(wall_ms_samples)
    print(
        f"[AI-4 authorize+injected] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    # 5초 지연이 주입되었으므로 p95가 50ms 예산을 크게 넘어야 함
    assert p95_ms > _AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS
