"""RequestDeployment(+Prepare) command.

Spec: AIOSproject #47 §3, #77 §2/§3.

Scope reduction (explicit, see migration docstring): #77 §2 splits REQUESTED->PREPARING
->READY into separate steps, but this leaf has no real async wait
(external provider approval, etc.) between the two transitions, so REQUEST and PREPARE
are combined into a single command — if provenance is valid and mandate is ACTIVE,
transition immediately to READY, otherwise FAILED.

package_ref is accepted as an opaque string only — FND-04(strategy_packages) has not yet
implemented the PAPER_ELIGIBLE package lifecycle itself (only up to validation-run),
so this leaf cannot actually validate package existence. When the package
lifecycle is implemented, only the package validation portion of this
function needs to be replaced."""
from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from src.foundation.mandates.api import MandateRevisionState
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.paper_control.contracts.v1 import DeploymentState as ContractState
from src.foundation.paper_control.contracts.v1 import PaperDeploymentView
from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CommandOutcome,
    CommandType,
    CredentialClass,
    DeploymentState,
    PaperDeployment,
)
from src.foundation.paper_control.domain.rules import InvalidProvenanceError, validate_provenance
from src.foundation.paper_control.ports.repository import PaperControlRepository


class NoActiveMandateError(Exception):
    pass


class IdempotencyKeyConflictError(Exception):
    """PAP-006 — A REQUEST with different content but the same idempotency_key arrived.
    True idempotency must only cache "retry of the same request" — silently swallowing
    a client bug that reused the key for a different request and returning a wrong
    deployment is unacceptable (discovered by full-audit agent platform-12)."""


def _compute_request_digest(
    *,
    package_ref: str,
    connection_id: UUID | None,
    adapter_type: str,
    provider_sandbox_account_ref: str,
    endpoint_classification: str,
) -> str:
    payload = {
        "package_ref": package_ref,
        "connection_id": str(connection_id) if connection_id is not None else None,
        "adapter_type": adapter_type,
        "provider_sandbox_account_ref": provider_sandbox_account_ref,
        "endpoint_classification": endpoint_classification,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def _replay_or_conflict(
    repo: PaperControlRepository,
    existing: PaperDeployment,
    *,
    digest: str,
    idempotency_key: str,
) -> PaperDeploymentView:
    if existing.request_digest != digest:
        raise IdempotencyKeyConflictError(
            f"idempotency_key={idempotency_key} is already used with different request content."
        )
    if existing.state == DeploymentState.FAILED:
        # Replay the original response for FAILED results as well (the original
        # request_deployment() raised an exception here, so retries must too).
        command = await repo.get_command_by_idempotency_key(existing.id, idempotency_key)
        detail = command.detail if command is not None else None
        raise InvalidProvenanceError(detail or "provenance validation failed")
    return deployment_to_view(existing)


def deployment_to_view(deployment: PaperDeployment) -> PaperDeploymentView:
    return PaperDeploymentView(
        id=deployment.id,
        package_ref=deployment.package_ref,
        connection_id=deployment.connection_id,
        state=ContractState(deployment.state.value),
        fence_token=deployment.fence_token,
        created_at=deployment.created_at,
        updated_at=deployment.updated_at,
    )


async def request_deployment(
    repo: PaperControlRepository,
    mandate_repo: MandateRepository,
    *,
    tenant_id: UUID,
    actor_subject_id: UUID,
    package_ref: str,
    connection_id: UUID | None,
    adapter_type: str,
    provider_sandbox_account_ref: str,
    endpoint_classification: str,
    idempotency_key: str,
) -> PaperDeploymentView:
    digest = _compute_request_digest(
        package_ref=package_ref,
        connection_id=connection_id,
        adapter_type=adapter_type,
        provider_sandbox_account_ref=provider_sandbox_account_ref,
        endpoint_classification=endpoint_classification,
    )
    # PAP-006 — Do not create a new deployment if one already exists with the same
    # (tenant_id, idempotency_key) (discovered by full-audit — previously this check
    # itself was missing, so every retry created a new deployment).
    existing = await repo.get_deployment_by_request_key(tenant_id, idempotency_key)
    if existing is not None:
        return await _replay_or_conflict(
            repo, existing, digest=digest, idempotency_key=idempotency_key
        )

    mandate = await mandate_repo.get_mandate(tenant_id)
    if mandate is None or mandate.active_revision_id is None:
        raise NoActiveMandateError(str(tenant_id))
    revision = await mandate_repo.get_revision(mandate.active_revision_id)
    if revision is None or revision.state != MandateRevisionState.ACTIVE:
        raise NoActiveMandateError(str(tenant_id))

    provenance = AdapterProvenance(
        adapter_type=adapter_type,
        credential_class=CredentialClass.PAPER,
        endpoint_classification=endpoint_classification,
        provider_sandbox_account_ref=provider_sandbox_account_ref,
    )

    # This leaf does not leave a separate REQUESTED intermediate row (scope reduction above —
    # REQUEST+PREPARE are combined into one command) — the provenance validation result
    # directly creates one READY or FAILED row.
    deployment_id = uuid4()
    detail: str | None = None
    try:
        validate_provenance(provenance)
        final_state = DeploymentState.READY
        outcome = CommandOutcome.ACCEPTED
    except InvalidProvenanceError as exc:
        final_state = DeploymentState.FAILED
        outcome = CommandOutcome.DENIED
        detail = str(exc)

    deployment = await repo.insert_deployment(
        PaperDeployment(
            id=deployment_id,
            tenant_id=tenant_id,
            connection_id=connection_id,
            package_ref=package_ref,
            mandate_revision_id=revision.id,
            provenance=provenance,
            state=final_state,
            fence_token=0,
            request_idempotency_key=idempotency_key,
            request_digest=digest,
        )
    )
    if deployment.id != deployment_id:
        # Lost a true concurrent request in the narrow window between the
        # pre-check above (get_deployment_by_request_key) and this INSERT —
        # insert_deployment() already returned the winner's row via ON CONFLICT
        # DO NOTHING + re-fetch. The command is already recorded on the winner's
        # side, so we don't write again here.
        return await _replay_or_conflict(
            repo, deployment, digest=digest, idempotency_key=idempotency_key
        )

    await repo.insert_command(
        deployment_id=deployment.id,
        idempotency_key=idempotency_key,
        command_type=CommandType.REQUEST,
        actor_subject_id=actor_subject_id,
        outcome=outcome,
        detail=detail,
    )
    if outcome is CommandOutcome.DENIED:
        raise InvalidProvenanceError(detail or "provenance validation failed")
    return deployment_to_view(deployment)
