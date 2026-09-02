"""Portfolio Mandate adversarial 테스트 — 75번 §5 MAN-007 "tenant/subject
mismatch and foreign mandate ref are denied without existence leak"."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.activate_revision import (
    CrossTenantMandateAccessError,
    RevisionNotFoundError,
)
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.foundation.mandates.projections import build_mandate_status_view
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.foundation.integration.mandates.conftest import default_rules
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool):
    return PostgresTrustRepository(pool)


async def test_cannot_activate_another_tenants_draft_revision(pool, repo, trust_repo):
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    owned_draft = await create_draft_mandate(
        repo, tenant_id=owner_id, subject_id=owner_id, rules=default_rules()
    )

    with pytest.raises(CrossTenantMandateAccessError):
        await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=attacker_id,
            subject_id=attacker_id,
            revision_id=owned_draft.id,
            reauthenticated=False,
        )

    # 공격 시도 이후에도 원 소유자의 revision은 여전히 DRAFT여야 한다.
    still_draft = await repo.get_revision(owned_draft.id)
    assert still_draft.state.value == "DRAFT"


async def test_activating_nonexistent_revision_raises_lookup_error(pool, repo, trust_repo):
    tenant_id = await create_test_user(pool)
    with pytest.raises(RevisionNotFoundError):
        await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=tenant_id,
            revision_id=uuid4(),
            reauthenticated=False,
        )


async def test_tenant_status_view_never_includes_another_tenants_mandate(pool, repo):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    await create_draft_mandate(
        repo, tenant_id=tenant_a, subject_id=tenant_a, rules=default_rules()
    )

    view_b = await build_mandate_status_view(repo, tenant_b)

    assert view_b.active_revision is None
    assert view_b.pending_revision is None
