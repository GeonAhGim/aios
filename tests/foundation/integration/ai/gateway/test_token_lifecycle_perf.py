"""AI-4 성능 테스트 -- authorize() DB 왕복 p95 단언 + 지연 주입 적색 검증.

성능 테스트는 test_token_lifecycle.py에서 분리(LOC 분할, task-18519).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
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
from tests._perf.relative_budget import RelativeBudget

_AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS = 50.0

_NOW = datetime.now(timezone.utc)


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
async def test_authorize_db_roundtrip_p95_within_budget(repo: PostgresAgentTokenRepository):
    """raw time.perf_counter() → RelativeBudget.measure_async 전환(task-10885).

    비동기 I/O 왕복은 time.process_time() CPU 시간으로 재면 대기 시간이
    통째로 사라지므로(실제 DB 지연을 못 재음), RelativeBudget.measure_async로
    wall-clock 기반 상대 비율 단언으로 바꾼다 — 보정 루프가 2배 느려지면
    DB 왕복도 스케줄링 경합으로 비례해 느려지므로 비율은 유지된다.
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

    samples: list[float] = []
    for _ in range(30):
        sample = await RelativeBudget().measure_async(
            lambda: authorize(repo, token_secret=issued.secret, scope=Scope.READ),
            n=1,
            warmup=0,
            calibration_n=1,
        )
        samples.append(sample.op_ms)

    p95_ms = _p95(samples)
    print(
        f"[AI-4 authorize] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS


@pytest.mark.perf
async def test_authorize_p95_detects_tenfold_delay_injection(
    repo: PostgresAgentTokenRepository, monkeypatch: pytest.MonkeyPatch
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

    # repo.get_by_hash()에 5초 지연 주입 — asyncpg 내부 await를 직접 대체
    original_get_by_hash = repo.get_by_hash

    async def delayed_get_by_hash(token_hash: str):
        await asyncio.sleep(5.0)
        return await original_get_by_hash(token_hash)

    monkeypatch.setattr(repo, "get_by_hash", delayed_get_by_hash)

    samples: list[float] = []
    for _ in range(5):
        sample = await RelativeBudget().measure_async(
            lambda: authorize(repo, token_secret=issued.secret, scope=Scope.READ),
            n=1,
            warmup=0,
            calibration_n=1,
        )
        samples.append(sample.op_ms)

    p95_ms = _p95(samples)
    print(
        f"[AI-4 authorize+injected] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    # 5초 지연이 주입되었으므로 p95가 50ms 예산을 크게 넘어야 함
    assert p95_ms > _AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS
