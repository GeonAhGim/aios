"""TEST-cov task-4620 — src/foundation/paper_control/adapters/postgres_repository.py
0% -> coverage. 실제 dev DB 대상(다른 paper_control 통합테스트와 동일 conftest).

DoD: negative test >=3, failure-injection >=1, docs/design/INVARIANTS.md 위반 없음
(105번 표준 conditional_update/increment_fence의 fail-closed 재확인일 뿐, 새 불변식
추가는 없다).

AUDIT_2026-09-30_auth_rls.md F1 (task-9455) 절 — tests/foundation/trust/
test_postgres_repository.py의 task-9453 패턴(aios_app_pool/aios_app_repo +
GUC 미바인딩 몽키패치)을 동일하게 적용한다."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CommandOutcome,
    CommandType,
    CredentialClass,
    DeploymentState,
    PaperDeployment,
    PaperOrderIntent,
)
from tests.foundation.integration.paper_control.conftest import request, tenant_with_mandate
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _run_as_aios_app(conn: asyncpg.Connection) -> None:
    """asyncpg pool `setup` hook: every connection handed out by this pool runs
    as the non-superuser `aios_app` role (task-9453/task-9455, AUDIT F1) so RLS
    is actually enforced — the module-level `pool` fixture (conftest.py)
    connects as the migrator/owner account, which PostgreSQL never subjects to
    RLS regardless of GUC binding."""
    await conn.execute("SET ROLE aios_app")


@pytest.fixture
async def aios_app_pool() -> asyncpg.Pool:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2, setup=_run_as_aios_app)
    yield p
    await p.close()


@pytest.fixture
def aios_app_repo(aios_app_pool: asyncpg.Pool) -> PostgresPaperControlRepository:
    return PostgresPaperControlRepository(aios_app_pool)


def _deployment(
    *, tenant_id, mandate_revision_id, idempotency_key: str, digest: str = "digest-1"
) -> PaperDeployment:
    return PaperDeployment(
        id=uuid4(),
        tenant_id=tenant_id,
        connection_id=None,
        package_ref="pkg-ref-cov",
        mandate_revision_id=mandate_revision_id,
        provenance=AdapterProvenance(
            adapter_type="fake-paper-v1",
            credential_class=CredentialClass.PAPER,
            endpoint_classification="SANDBOX",
            provider_sandbox_account_ref="sandbox-acct-cov",
        ),
        state=DeploymentState.REQUESTED,
        fence_token=0,
        request_idempotency_key=idempotency_key,
        request_digest=digest,
    )


async def _active_revision_id(mandate_repo, tenant_id):
    mandate = await mandate_repo.get_mandate(tenant_id)
    assert mandate is not None and mandate.active_revision_id is not None
    return mandate.active_revision_id


# --- negative: unknown id/key lookups return None instead of raising ---


async def test_get_deployment_returns_none_for_unknown_id(repo):
    assert await repo.get_deployment(uuid4()) is None


async def test_get_deployment_by_request_key_returns_none_for_unknown_key(
    pool, repo, mandate_repo, trust_repo
):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    assert await repo.get_deployment_by_request_key(tenant_id, "no-such-key") is None


async def test_get_command_by_idempotency_key_returns_none_for_unknown_key(repo):
    assert await repo.get_command_by_idempotency_key(uuid4(), "no-such-key") is None


async def test_list_deployments_empty_for_tenant_without_deployments(pool, repo):
    tenant_id = await create_test_tenant(pool)
    assert await repo.list_deployments(tenant_id) == []


# --- negative: conditional-write guards reject a stale expected_state (105 표준, fail-closed) ---


async def test_transition_deployment_state_wrong_expected_state_raises(
    pool, repo, mandate_repo, trust_repo
):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-transition")
    assert deployment.state.value == "READY"

    with pytest.raises(ConcurrencyConflictError):
        await repo.transition_deployment_state(
            deployment.id, tenant_id=tenant_id, expected_state="RUNNING", new_state="STOPPED"
        )
    # state untouched by the rejected write
    unchanged = await repo.get_deployment(deployment.id)
    assert unchanged is not None and unchanged.state.value == "READY"


async def test_increment_fence_wrong_expected_state_raises(pool, repo, mandate_repo, trust_repo):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-fence")

    with pytest.raises(ConcurrencyConflictError):
        await repo.increment_fence(
            deployment.id, tenant_id=tenant_id, expected_state="RUNNING", new_state="PAUSED"
        )
    unchanged = await repo.get_deployment(deployment.id)
    assert unchanged is not None and unchanged.fence_token == 0


# --- positive: insert/read round-trips not covered by the higher-level lifecycle tests ---


async def test_insert_deployment_duplicate_idempotency_key_returns_existing_row(
    pool, repo, mandate_repo, trust_repo
):
    """PAP-006 race path — ON CONFLICT DO NOTHING + re-read (postgres_repository.py
    insert_deployment, "Lost the race" branch). request_deployment() itself
    short-circuits via get_deployment_by_request_key before ever calling
    insert_deployment twice, so this exercises the repository directly to reach
    the branch the application layer never triggers."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    revision_id = await _active_revision_id(mandate_repo, tenant_id)

    first = await repo.insert_deployment(
        _deployment(tenant_id=tenant_id, mandate_revision_id=revision_id, idempotency_key="dup-key")
    )
    second = await repo.insert_deployment(
        _deployment(tenant_id=tenant_id, mandate_revision_id=revision_id, idempotency_key="dup-key")
    )
    assert first.id == second.id
    assert first.request_idempotency_key == second.request_idempotency_key == "dup-key"


async def test_insert_command_and_get_by_idempotency_key(pool, repo, mandate_repo, trust_repo):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-command")

    inserted = await repo.insert_command(
        deployment_id=deployment.id,
        idempotency_key="cmd-key-1",
        command_type=CommandType.START,
        actor_subject_id=tenant_id,
        outcome=CommandOutcome.ACCEPTED,
        detail="ok",
    )
    assert inserted.command_type == CommandType.START
    assert inserted.outcome == CommandOutcome.ACCEPTED

    fetched = await repo.get_command_by_idempotency_key(deployment.id, "cmd-key-1")
    assert fetched is not None
    assert fetched.id == inserted.id
    assert fetched.detail == "ok"


async def test_insert_order_intent(pool, repo, mandate_repo, trust_repo):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-intent")

    intent = await repo.insert_order_intent(
        PaperOrderIntent(
            id=uuid4(),
            deployment_id=deployment.id,
            sequence=1,
            fence_token_at_submit=0,
            state="PENDING",
        )
    )
    assert intent.deployment_id == deployment.id
    assert intent.sequence == 1
    assert intent.fence_token_at_submit == 0
    assert intent.state == "PENDING"


async def test_list_running_deployments_excludes_ready_state(pool, repo, mandate_repo, trust_repo):
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    deployment = await request(repo, mandate_repo, tenant_id, key_suffix="-running")
    assert deployment.state.value == "READY"

    running_ids = [d.id for d in await repo.list_running_deployments()]
    assert deployment.id not in running_ids


# --- failure injection: pool-level errors propagate instead of being swallowed (fail-closed) ---


async def test_get_deployment_propagates_pool_connection_error(repo, monkeypatch):
    class _BoomPool:
        def acquire(self):
            raise asyncpg.PostgresConnectionError("boom")

    monkeypatch.setattr(repo, "_pool", _BoomPool())
    with pytest.raises(asyncpg.PostgresConnectionError):
        await repo.get_deployment(uuid4())


# ============================================================================
# AUDIT_2026-09-30_auth_rls.md F1 (task-9455) — tenant_transaction/GUC binding
# real repository call path (get_deployment_by_request_key), not the
# AppRoleTx-direct-SQL route test_rls_foundation.py uses.
# ============================================================================


async def test_get_deployment_by_request_key_returns_none_under_aios_app_role_when_guc_unbound(
    pool, repo, aios_app_repo, mandate_repo, trust_repo, monkeypatch
):
    """negative / F1 재현: 감사가 지적한 정정 전 상태(GUC 미바인딩)를 회귀
    테스트로 고정한다. tenant_transaction을 GUC를 세팅하지 않는
    `pool.acquire()` 동급 버전으로 몽키패치해 정정 전 어댑터를 재현하면,
    비-superuser `aios_app` role 아래에서는 소유 tenant 자신의 배포조차
    0행(None)으로 막힌다 — RLS 정책이 SQL의 tenant 조건이 아니라 이 GUC로
    판정하기 때문이다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    revision_id = await _active_revision_id(mandate_repo, tenant_id)
    await repo.insert_deployment(
        _deployment(
            tenant_id=tenant_id,
            mandate_revision_id=revision_id,
            idempotency_key="f1-guc-unbound",
        )
    )

    @asynccontextmanager
    async def _unbound_transaction(pool, tenant_id):
        # F1 정정 전 어댑터와 동급: 연결은 열지만 app.tenant_id GUC를 세팅하지 않는다.
        async with pool.acquire() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(
        "src.foundation.paper_control.adapters.postgres_repository.tenant_transaction",
        _unbound_transaction,
    )

    result = await aios_app_repo.get_deployment_by_request_key(tenant_id, "f1-guc-unbound")

    assert result is None  # F1: GUC 미바인딩이면 자기 행도 0행


async def test_get_deployment_by_request_key_returns_row_under_aios_app_role_once_guc_bound(
    pool, repo, aios_app_repo, mandate_repo, trust_repo
):
    """감사 재현의 나머지 절반: 몽키패치 없이(즉 정정된 어댑터로) 같은 조회를
    같은 aios_app role에서 실행하면 GUC가 바인딩되어 1행이 돌아온다 — F1이
    실제로 고쳐졌다는 양성 증거."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    revision_id = await _active_revision_id(mandate_repo, tenant_id)
    inserted = await repo.insert_deployment(
        _deployment(
            tenant_id=tenant_id,
            mandate_revision_id=revision_id,
            idempotency_key="f1-guc-bound",
        )
    )

    result = await aios_app_repo.get_deployment_by_request_key(tenant_id, "f1-guc-bound")

    assert result is not None
    assert result.id == inserted.id


async def test_get_deployment_by_request_key_cross_tenant_returns_none_under_aios_app_role(
    pool, repo, aios_app_repo, mandate_repo, trust_repo
):
    """negative: tenant A GUC로 tenant B의 행을 조회하면 0행이어야 한다
    (교차 테넌트 격리) — WHERE tenant_id 조건과 RLS 정책이 이중으로 막는다."""
    tenant_a = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    tenant_b = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    revision_id = await _active_revision_id(mandate_repo, tenant_a)
    await repo.insert_deployment(
        _deployment(
            tenant_id=tenant_a,
            mandate_revision_id=revision_id,
            idempotency_key="f1-cross-tenant",
        )
    )

    result = await aios_app_repo.get_deployment_by_request_key(tenant_b, "f1-cross-tenant")

    assert result is None
