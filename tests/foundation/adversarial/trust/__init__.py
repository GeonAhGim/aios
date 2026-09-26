"""Trust Core adversarial 테스트 — 순수 규칙 함수
(`src/foundation/trust/domain/rules/__init__.py`)에 대한 negative/실패주입
케이스. DB 없이 도는 단위 테스트라 이 패키지 `__init__.py`에 직접 둔다
(task-7935 DEEPEN, 원 리프 task-6704 — paper_control/validation 패키지의
동일 패턴 참조).

Spec: AIOSproject 73_trust_core_l3_build_and_operational_specification_v1.0.md
§3.1(멤버십 상태 전이표), §6(신선도 규칙).

INVARIANTS.md: 이 모듈은 주문/실행 경로가 아니라 tenancy/consent 도메인이라
I-01..I-12 중 직접 대응하는 항목이 없다 — N/A(주문/실행 불변식 범위 밖).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.foundation.trust.domain.models import (
    Consent,
    ConsentState,
    Disclosure,
    MembershipRole,
    MembershipState,
)
from src.foundation.trust.domain.rules import (
    freshness_denial_reason,
    is_membership_transition_allowed,
    would_remove_last_owner,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _disclosure(**overrides) -> Disclosure:
    fields = dict(
        id=uuid4(),
        purpose="p1",
        revision=1,
        content_hash="hash-1",
        published_at=_NOW - timedelta(days=1),
        retired_at=None,
    )
    fields.update(overrides)
    return Disclosure(**fields)


def _consent(**overrides) -> Consent:
    fields = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        subject_id=uuid4(),
        purpose="p1",
        disclosure_id=uuid4(),
        disclosure_revision=1,
        state=ConsentState.ACTIVE,
        accepted_at=_NOW - timedelta(hours=1),
        revoked_at=None,
        expires_at=None,
    )
    fields.update(overrides)
    return Consent(**fields)


# --- negative tests (spec 73 §6 신선도 규칙 위반 입력 거부) -------------------


def test_freshness_denial_reason_rejects_missing_consent_as_policy_required():
    """동의 레코드 자체가 없으면 POLICY_CONSENT_REQUIRED로 거부한다."""
    reason = freshness_denial_reason(None, required_disclosure=_disclosure(), now=_NOW)
    assert reason == "POLICY_CONSENT_REQUIRED"


def test_freshness_denial_reason_rejects_revoked_consent():
    """REVOKED 상태의 동의는 재활용될 수 없다 — spec 73 §3.2 append-only."""
    consent = _consent(state=ConsentState.REVOKED)
    reason = freshness_denial_reason(consent, required_disclosure=_disclosure(), now=_NOW)
    assert reason == "POLICY_CONSENT_REVOKED"


def test_freshness_denial_reason_rejects_expired_consent_even_if_state_active():
    """만료 시각이 지난 동의는 state가 아직 ACTIVE라도 즉시 무효다."""
    consent = _consent(expires_at=_NOW - timedelta(seconds=1))
    reason = freshness_denial_reason(consent, required_disclosure=_disclosure(), now=_NOW)
    assert reason == "POLICY_CONSENT_EXPIRED"


def test_is_membership_transition_allowed_rejects_same_state_transition():
    """전이표에 없는 '같은 상태로의 전이'는 어떤 역할이든 거부된다
    (spec 73 §3.1 — 표 부재는 곧 거부)."""
    assert (
        is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.ACTIVE, actor_role=MembershipRole.OWNER
        )
        is False
    )


def test_would_remove_last_owner_rejects_revoking_sole_active_owner():
    """마지막 활성 OWNER를 REVOKED로 내리는 시도는 거부 대상으로 표시된다
    (spec 73 §3.1 "cannot remove last owner" 가드)."""
    assert (
        would_remove_last_owner(active_owners=1, target_is_owner=True, to=MembershipState.REVOKED)
        is True
    )


# --- 실패주입: 전이표 조회 자체가 깨지면 조용히 통과시키지 않는다 -------------


def test_is_membership_transition_allowed_propagates_lookup_failure(monkeypatch):
    """전이표(dict) 조회가 예외를 던지면(예: 손상된 매핑) fail-closed로
    그대로 전파해야 한다 — 조용한 기본 허용/거부로 대체되면 안 된다."""
    import src.foundation.trust.domain.rules as rules_module

    class _BoomMapping:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("injected transition table failure")

    monkeypatch.setattr(rules_module, "_MEMBERSHIP_TRANSITIONS", _BoomMapping())

    with pytest.raises(RuntimeError, match="injected transition table failure"):
        rules_module.is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.SUSPENDED, actor_role=MembershipRole.ADMIN
        )
