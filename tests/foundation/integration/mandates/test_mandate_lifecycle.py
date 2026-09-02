"""FND-02 Portfolio Mandate 통합테스트 — 실제 dev DB 대상. 71번 §7 "정상 흐름 +
negative test"."""
import asyncio
import time
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.get_audit_timeline import get_audit_timeline
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.activate_revision import (
    CoolingOffNotElapsedError,
    MaterialChangeRequiresFreshConsentError,
    MaterialChangeRequiresReauthError,
)
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import (
    MandateAlreadyExistsError,
    create_draft_mandate,
)
from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_policy_command
from src.foundation.mandates.application.pause_mandate import pause_mandate, resume_mandate
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.foundation.mandates.contracts.v1 import (
    MandateRevisionState,
    PolicyEvaluationSubject,
    PolicyOutcome,
)
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.application.accept_disclosure import accept_disclosure
from src.foundation.trust.contracts.v1 import TenantContext as TrustTenantContext
from tests.foundation.integration.mandates.conftest import backdate_cooling_off, default_rules
from tests.foundation.integration.trust.conftest import create_disclosure
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


@pytest.fixture
def audit_repo(pool):
    return PostgresAuditEventRepository(pool)


async def _tenant(pool):
    return await create_test_user(pool)


_material_change_disclosure_revision_counter = int(time.time())


def _next_material_change_disclosure_revision() -> int:
    """`get_active_disclosure()`(FND-01)는 purpose당 revision이 가장 큰 것 하나를
    "최신"으로 취급한다 — 무작위 값은 나중에 실행되는 테스트가 먼저 실행된
    테스트보다 우연히 더 작은 값을 뽑으면, 자기가 방금 동의한 게 아니라 다른
    테스트의 (숫자만 더 큰) revision이 "최신"이 돼버려 곧바로
    POLICY_CONSENT_STALE_REVISION으로 실패한다. 단조증가 카운터를 쓰면 이 파일
    안에서 실행 순서대로 항상 "지금 막 만든 게 최신"이 보장된다."""
    global _material_change_disclosure_revision_counter
    _material_change_disclosure_revision_counter += 1
    return _material_change_disclosure_revision_counter


async def _consent_to_material_change_disclosure(pool, trust_repo, tenant_id) -> None:
    """MATERIAL_CHANGE_CONSENT_PURPOSE(activate_revision.py)는 앱 상수라 여러
    테스트가 같은 purpose 문자열을 공유한다 — disclosure.revision을 매번 단조
    증가시켜 `UNIQUE(purpose, revision)` 충돌 없이, 그리고 "최신" 판정이 항상
    이 호출 쪽을 가리키게 한다."""
    revision = _next_material_change_disclosure_revision()
    await create_disclosure(pool, purpose="portfolio_mandate_material_change", revision=revision)
    trust_context = TrustTenantContext(
        tenant_id=tenant_id, subject_id=tenant_id, mfa_verified=True
    )
    await accept_disclosure(
        trust_repo,
        trust_context,
        purpose="portfolio_mandate_material_change",
        disclosure_revision=revision,
    )


async def test_create_draft_then_activate_first_revision_needs_no_reauth(pool, repo, trust_repo):
    tenant_id = await _tenant(pool)

    draft = await create_draft_mandate(
        repo, tenant_id=tenant_id, subject_id=tenant_id, rules=default_rules()
    )
    assert draft.state == MandateRevisionState.DRAFT
    assert draft.revision_no == 1

    activated = await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=draft.id,
        reauthenticated=False,
    )
    assert activated.state == MandateRevisionState.ACTIVE


async def test_create_draft_twice_without_activating_raises(pool, repo):
    tenant_id = await _tenant(pool)
    await create_draft_mandate(
        repo, tenant_id=tenant_id, subject_id=tenant_id, rules=default_rules()
    )
    with pytest.raises(MandateAlreadyExistsError):
        await create_draft_mandate(
            repo, tenant_id=tenant_id, subject_id=tenant_id, rules=default_rules()
        )


async def test_evaluate_policy_without_mandate_raises(pool, repo):
    tenant_id = await _tenant(pool)
    with pytest.raises(NoActiveMandateError):
        await evaluate_policy_command(
            repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="x")
        )


async def _activated_tenant(pool, repo, trust_repo, **rule_overrides):
    tenant_id = await _tenant(pool)
    draft = await create_draft_mandate(
        repo, tenant_id=tenant_id, subject_id=tenant_id, rules=default_rules(**rule_overrides)
    )
    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=draft.id,
        reauthenticated=False,
    )
    return tenant_id


async def test_evaluate_policy_allows_within_limits(pool, repo, trust_repo):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    decision = await evaluate_policy_command(
        repo,
        tenant_id=tenant_id,
        subject=PolicyEvaluationSubject(command_type="paper_deployment", total_exposure_pct=10.0),
    )
    assert decision.outcome == PolicyOutcome.ALLOW
    assert decision.obligations == ["REQUIRE_RISK_GATE"]


async def test_evaluate_policy_denies_forbidden_asset(pool, repo, trust_repo):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    decision = await evaluate_policy_command(
        repo,
        tenant_id=tenant_id,
        subject=PolicyEvaluationSubject(command_type="x", asset="XYZ"),
    )
    assert decision.outcome == PolicyOutcome.DENY
    assert "POLICY_FORBIDDEN_ASSET" in decision.reason_codes


async def test_evaluate_policy_caches_by_fingerprint(pool, repo, trust_repo):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    subject = PolicyEvaluationSubject(command_type="x", asset="XYZ")

    first = await evaluate_policy_command(repo, tenant_id=tenant_id, subject=subject)
    second = await evaluate_policy_command(repo, tenant_id=tenant_id, subject=subject)

    assert first.id == second.id  # 재계산 없이 캐시된 동일 decision 반환


async def test_evaluate_policy_denies_when_mandate_paused(pool, repo, trust_repo):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    await pause_mandate(repo, tenant_id=tenant_id)

    decision = await evaluate_policy_command(
        repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="x")
    )
    assert decision.outcome == PolicyOutcome.PAUSE_REQUIRED
    assert "STATE_MANDATE_PAUSED" in decision.reason_codes


async def test_pause_immediately_invalidates_a_warm_cache(pool, repo, trust_repo):
    """레드팀 지적(agent-platform-12) 회귀 테스트 — fingerprint가 tenant_id+
    subject만 해시하던 시절엔, 캐시가 이미 ALLOW로 데워진 상태에서 pause해도
    같은 subject 재평가가 최대 DECISION_CACHE_TTL_SECONDS초 동안 그 stale
    ALLOW를 그대로 돌려줬다. fingerprint에 revision id+state를 넣은 뒤로는
    pause 직후 첫 재평가부터 곧바로 PAUSE_REQUIRED여야 한다(별도 캐시
    무효화 호출 없이)."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    subject = PolicyEvaluationSubject(command_type="x")

    warm = await evaluate_policy_command(repo, tenant_id=tenant_id, subject=subject)
    assert warm.outcome == PolicyOutcome.ALLOW

    await pause_mandate(repo, tenant_id=tenant_id)

    decision = await evaluate_policy_command(repo, tenant_id=tenant_id, subject=subject)
    assert decision.outcome == PolicyOutcome.PAUSE_REQUIRED
    assert decision.id != warm.id


async def test_resume_mandate_allows_evaluation_again(pool, repo, trust_repo):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    await pause_mandate(repo, tenant_id=tenant_id)
    resumed = await resume_mandate(repo, tenant_id=tenant_id)
    assert resumed.state == MandateRevisionState.ACTIVE

    decision = await evaluate_policy_command(
        repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="x")
    )
    assert decision.outcome == PolicyOutcome.ALLOW


async def test_pausing_already_paused_mandate_raises_concurrency_conflict(pool, repo, trust_repo):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    await pause_mandate(repo, tenant_id=tenant_id)
    with pytest.raises(ConcurrencyConflictError):
        await pause_mandate(repo, tenant_id=tenant_id)


async def test_non_material_amendment_activates_without_reauth(pool, repo, trust_repo):
    """더 보수적으로만 바뀌는 개정은 material change가 아니므로 재인증 없이
    바로 activate 가능해야 한다."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)

    proposed = await propose_amendment(
        repo, tenant_id=tenant_id, rules=default_rules(max_total_exposure_pct=50.0)
    )
    assert proposed.cooling_off_started_at is None

    activated = await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=proposed.id,
        reauthenticated=False,
    )
    assert activated.state == MandateRevisionState.ACTIVE
    assert activated.max_total_exposure_pct == 50.0


async def test_material_amendment_without_reauth_is_blocked(pool, repo, trust_repo):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    proposed = await propose_amendment(
        repo, tenant_id=tenant_id, rules=default_rules(max_total_exposure_pct=95.0)
    )
    assert proposed.cooling_off_started_at is not None

    with pytest.raises(MaterialChangeRequiresReauthError):
        await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=tenant_id,
            revision_id=proposed.id,
            reauthenticated=False,
        )


async def test_material_amendment_without_trust_consent_is_blocked(pool, repo, trust_repo):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    proposed = await propose_amendment(
        repo, tenant_id=tenant_id, rules=default_rules(max_total_exposure_pct=95.0)
    )

    with pytest.raises(MaterialChangeRequiresFreshConsentError):
        await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=tenant_id,
            revision_id=proposed.id,
            reauthenticated=True,
        )


async def test_material_amendment_before_cooling_off_elapsed_is_blocked(pool, repo, trust_repo):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    # reauth + 최신 동의까지는 통과시키고, cooling-off만 아직 안 지난 상태를 재현한다.
    await _consent_to_material_change_disclosure(pool, trust_repo, tenant_id)

    proposed = await propose_amendment(
        repo, tenant_id=tenant_id, rules=default_rules(max_total_exposure_pct=95.0)
    )

    with pytest.raises(CoolingOffNotElapsedError):
        await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=tenant_id,
            revision_id=proposed.id,
            reauthenticated=True,
        )


async def test_material_amendment_after_all_gates_pass_activates_and_supersedes(
    pool, repo, trust_repo
):
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    await _consent_to_material_change_disclosure(pool, trust_repo, tenant_id)

    old_active = await repo.get_active_revision((await repo.get_mandate(tenant_id)).id)
    proposed = await propose_amendment(
        repo, tenant_id=tenant_id, rules=default_rules(max_total_exposure_pct=95.0)
    )
    await backdate_cooling_off(pool, proposed.id, seconds_ago=120)

    activated = await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=proposed.id,
        reauthenticated=True,
    )
    assert activated.state == MandateRevisionState.ACTIVE
    assert activated.max_total_exposure_pct == 95.0

    old_after = await repo.get_revision(old_active.id)
    assert old_after.state.value == "SUPERSEDED"


async def test_concurrent_activation_of_two_proposed_revisions_only_one_succeeds(
    pool, repo, trust_repo
):
    """MAN-004 — 같은 mandate에 대해 서로 다른 두 revision을 동시에 activate하면
    정확히 하나만 성공해야 한다(105번 §4 형태 A)."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    revision_a = await propose_amendment(
        repo, tenant_id=tenant_id, rules=default_rules(max_total_exposure_pct=10.0)
    )
    revision_b = await propose_amendment(
        repo, tenant_id=tenant_id, rules=default_rules(max_single_instrument_pct=5.0)
    )

    async def attempt(revision_id):
        return await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=tenant_id,
            revision_id=revision_id,
            reauthenticated=False,
        )

    results = await asyncio.gather(
        attempt(revision_a.id), attempt(revision_b.id), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) == 1


async def test_pause_resume_record_audit_events(pool, repo, trust_repo, audit_repo):
    """전수감사 §6 회귀 — pause/resume이 실제 감사 이벤트를 남기는지."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)

    await pause_mandate(repo, tenant_id=tenant_id, audit_repo=audit_repo)
    await resume_mandate(repo, tenant_id=tenant_id, audit_repo=audit_repo)

    page = await get_audit_timeline(audit_repo, tenant_id=tenant_id, limit=10)
    actions = [e.action for e in page.items]
    assert "mandate_paused" in actions
    assert "mandate_resumed" in actions
