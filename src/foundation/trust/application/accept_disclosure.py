"""AcceptDisclosure command.

Spec: AIOSproject §4 (`AcceptDisclosure` -> `trust.consent_accepted.v1`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.foundation.trust.contracts.v1 import ConsentDecision, ConsentState, TenantContext
from src.foundation.trust.domain.rules import is_disclosure_acceptable
from src.foundation.trust.ports.repository import TrustRepository

# §3.2 "ACTIVE becomes unusable after expires_at" — no spec document specifies
# a concrete validity period (compliance policy value, out of scope for this leaf).
# Discovered during full-audit (agent-platform-12): previously this was always None,
# meaning the expiration check (is_consent_fresh in domain/rules.py, already
# correctly implemented and tested) never actually fired. Provide a named default
# so that logic stays alive — use 12 months, a common cycle for financial consumer
# consent re-confirmation, and isolate it to a single constant so compliance review
# needs only one change.
DEFAULT_CONSENT_VALIDITY = timedelta(days=365)


class DisclosureRetiredError(Exception):
    """73§4 VALIDATION_DISCLOSURE_RETIRED — cannot consent to a retired disclosure."""


class DisclosureNotFoundError(Exception):
    pass


class ConsentAlreadyActiveError(Exception):
    """73§4 STATE_DUPLICATE_COMMAND — an ACTIVE consent already exists for the same
    purpose/revision (translates the uq_consent_record_active_purpose unique index
    violation into a domain exception)."""


async def accept_disclosure(
    repo: TrustRepository,
    context: TenantContext,
    *,
    purpose: str,
    disclosure_revision: int,
) -> ConsentDecision:
    disclosure = await repo.get_disclosure_by_purpose_and_revision(purpose, disclosure_revision)
    if disclosure is None:
        raise DisclosureNotFoundError(f"{purpose} revision={disclosure_revision}")

    now = datetime.now(timezone.utc)
    if not is_disclosure_acceptable(disclosure, now=now):
        raise DisclosureRetiredError(f"{purpose} revision={disclosure_revision}은(는) 폐기됨")

    existing = await repo.get_active_consent(context.tenant_id, purpose)
    if existing is not None:
        if existing.disclosure_revision == disclosure_revision:
            raise ConsentAlreadyActiveError(f"{purpose} revision={disclosure_revision}")
        # §3.2 — a new revision requires a new ACTIVE record and does not overwrite
        # the previous one (append-only). However, uq_consent_record_active_purpose
        # allows only one ACTIVE per (tenant_id, purpose), so we first transition
        # the previous revision's record to REVOKED — since revoke_consent() uses a
        # conditional UPDATE(state=ACTIVE), if another request processed first,
        # it safely fails with ConcurrencyConflictError (§2 of the 105 pattern).
        await repo.revoke_consent(existing.id, tenant_id=context.tenant_id)

    consent = await repo.insert_consent(
        tenant_id=context.tenant_id,
        subject_id=context.subject_id,
        purpose=purpose,
        disclosure_id=disclosure.id,
        disclosure_revision=disclosure_revision,
        expires_at=now + DEFAULT_CONSENT_VALIDITY,
    )
    return ConsentDecision(
        consent_id=consent.id,
        tenant_id=consent.tenant_id,
        purpose=consent.purpose,
        disclosure_id=consent.disclosure_id,
        disclosure_revision=consent.disclosure_revision,
        state=ConsentState(consent.state.value),
        accepted_at=consent.accepted_at,
        revoked_at=consent.revoked_at,
        expires_at=consent.expires_at,
    )
