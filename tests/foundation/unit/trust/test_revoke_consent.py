"""revoke_consent() 단위테스트 — FakeRepository로 DB 없이 매핑/예외 경로를 검증한다.

71번 §7 "정상 흐름 + negative test", task-4689 D2: negative test >=3,
실패주입 테스트 1건 이상.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.trust.application.revoke_consent import (
    CrossTenantConsentAccessError,
    revoke_consent,
)
from src.foundation.trust.contracts.v1 import ConsentState, TenantContext
from src.foundation.trust.domain.models import Consent
from src.foundation.trust.domain.models import ConsentState as DomainConsentState


def _context(tenant_id=None) -> TenantContext:
    tid = tenant_id or uuid4()
    return TenantContext(tenant_id=tid, subject_id=tid, role="OWNER", mfa_verified=False)


def _consent(tenant_id, *, state=DomainConsentState.REVOKED) -> Consent:
    now = datetime.now(timezone.utc)
    return Consent(
        id=uuid4(),
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose="p",
        disclosure_id=uuid4(),
        disclosure_revision=1,
        state=state,
        accepted_at=now,
        revoked_at=now,
        expires_at=None,
    )


class FakeRepository:
    def __init__(self, *, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.calls: list[tuple] = []

    async def revoke_consent(self, consent_id, *, tenant_id):
        self.calls.append((consent_id, tenant_id))
        if self._raises is not None:
            raise self._raises
        return self._result


async def test_revoke_consent_maps_domain_consent_to_contract_decision():
    context = _context()
    consent = _consent(context.tenant_id)
    repo = FakeRepository(result=consent)

    result = await revoke_consent(repo, context, consent_id=consent.id)

    assert result.consent_id == consent.id
    assert result.tenant_id == consent.tenant_id
    assert result.purpose == consent.purpose
    assert result.disclosure_id == consent.disclosure_id
    assert result.disclosure_revision == consent.disclosure_revision
    assert result.state == ConsentState.REVOKED
    assert result.revoked_at == consent.revoked_at
    assert result.expires_at is None


async def test_revoke_consent_passes_consent_id_and_tenant_id_to_repo():
    context = _context()
    consent = _consent(context.tenant_id)
    repo = FakeRepository(result=consent)

    await revoke_consent(repo, context, consent_id=consent.id)

    assert repo.calls == [(consent.id, context.tenant_id)]


async def test_revoke_consent_converts_permission_error_to_cross_tenant_error():
    """negative — 다른 tenant의 consent_id로 시도하면 repo가 PermissionError를
    던지고, 애플리케이션 레이어는 이를 CrossTenantConsentAccessError로 변환해야 한다."""
    context = _context()
    repo = FakeRepository(raises=PermissionError("tenant mismatch"))

    with pytest.raises(CrossTenantConsentAccessError):
        await revoke_consent(repo, context, consent_id=uuid4())


async def test_revoke_consent_preserves_permission_error_message():
    """negative — 경계값: 예외 메시지가 유실되지 않고 그대로 전달되는지 확인."""
    context = _context()
    repo = FakeRepository(raises=PermissionError("AUTH_TENANT_MISMATCH"))

    with pytest.raises(CrossTenantConsentAccessError, match="AUTH_TENANT_MISMATCH"):
        await revoke_consent(repo, context, consent_id=uuid4())


async def test_revoke_consent_does_not_catch_lookup_error():
    """negative — 존재하지 않는 consent_id는 LookupError로 그대로 전파되어야
    한다(CrossTenantConsentAccessError로 잘못 변환되면 안 된다)."""
    context = _context()
    repo = FakeRepository(raises=LookupError("not found"))

    with pytest.raises(LookupError):
        await revoke_consent(repo, context, consent_id=uuid4())


async def test_revoke_consent_propagates_unexpected_repository_failure():
    """실패주입 — repo.revoke_consent가 예상 밖 예외(RuntimeError, 예: DB 커넥션
    끊김)를 던지면 revoke_consent()는 이를 삼키거나 다른 타입으로 위장하지
    않고 그대로 전파해야 한다(fail-closed)."""
    context = _context()
    repo = FakeRepository(raises=RuntimeError("connection lost"))

    with pytest.raises(RuntimeError, match="connection lost"):
        await revoke_consent(repo, context, consent_id=uuid4())
