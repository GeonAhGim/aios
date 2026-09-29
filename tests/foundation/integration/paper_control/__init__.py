"""Paper_control integration package marker + DEEPEN negative/failure-injection/perf tests.

Task-7948 DEEPEN of task-6704 (고아 산출물 회수 5828 (qa-2)) — this package had
0 negative tests and no failure-injection/perf markers. Sibling files
(test_paper_deployment_lifecycle.py, test_paper_deployment_idempotency.py,
test_paper_deployment_safety.py) already exercise the real-Postgres
happy-path/idempotency/kill-switch scenarios; this module adds tests against
an in-memory fake of the `PaperControlRepository` Protocol so they run
without `TEST_DATABASE_URL`, covering pure-domain rejection paths
(`domain.rules.validate_provenance`) and application-layer state/ownership
guards (`pause_deployment.py`) that the DB-backed tests don't isolate."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.foundation.paper_control.application.pause_deployment import (
    CrossTenantDeploymentAccessError,
    DeploymentNotFoundError,
    InvalidDeploymentStateError,
    pause_deployment,
    stop_deployment,
)
from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CommandOutcome,
    CommandType,
    CredentialClass,
    DeploymentCommand,
    DeploymentState,
    PaperDeployment,
)
from src.foundation.paper_control.domain.rules import InvalidProvenanceError, validate_provenance


class _FakePaperControlRepository:
    """Minimal in-memory stand-in for `PaperControlRepository`
    (ports/repository.py). Only implements what these tests exercise
    (`get_deployment`/`get_command_by_idempotency_key`/`insert_command`/
    `increment_fence`) — not a general-purpose fake."""

    def __init__(self) -> None:
        self.deployments: dict[UUID, PaperDeployment] = {}
        self.commands: dict[tuple[UUID, str], DeploymentCommand] = {}

    def seed(self, deployment: PaperDeployment) -> None:
        self.deployments[deployment.id] = deployment

    async def get_deployment(self, deployment_id: UUID) -> PaperDeployment | None:
        return self.deployments.get(deployment_id)

    async def get_command_by_idempotency_key(
        self, deployment_id: UUID, idempotency_key: str
    ) -> DeploymentCommand | None:
        return self.commands.get((deployment_id, idempotency_key))

    async def insert_command(
        self,
        *,
        deployment_id: UUID,
        idempotency_key: str,
        command_type: CommandType,
        actor_subject_id: UUID,
        outcome: CommandOutcome,
        detail: str | None,
    ) -> DeploymentCommand:
        command = DeploymentCommand(
            id=uuid4(),
            deployment_id=deployment_id,
            idempotency_key=idempotency_key,
            command_type=command_type,
            actor_subject_id=actor_subject_id,
            outcome=outcome,
            detail=detail,
            created_at=datetime.now(timezone.utc),
        )
        self.commands[(deployment_id, idempotency_key)] = command
        return command

    async def increment_fence(
        self, deployment_id: UUID, *, expected_state: str, new_state: str
    ) -> PaperDeployment:
        current = self.deployments[deployment_id]
        assert current.state.value == expected_state
        updated = replace(
            current, state=DeploymentState(new_state), fence_token=current.fence_token + 1
        )
        self.deployments[deployment_id] = updated
        return updated


def _make_deployment(*, tenant_id: UUID, state: DeploymentState) -> PaperDeployment:
    now = datetime.now(timezone.utc)
    return PaperDeployment(
        id=uuid4(),
        tenant_id=tenant_id,
        connection_id=None,
        package_ref="pkg-ref-1",
        mandate_revision_id=uuid4(),
        provenance=AdapterProvenance(
            adapter_type="fake-paper-v1",
            credential_class=CredentialClass.PAPER,
            endpoint_classification="SANDBOX",
            provider_sandbox_account_ref="sandbox-acct-1",
        ),
        state=state,
        fence_token=0,
        created_at=now,
        updated_at=now,
    )


# ── Negative tests: pure-domain rejection (`validate_provenance`) ──────────


class _LiveCredentialClass:
    """Stand-in with a `.value` attribute but no identity/equality with
    `CredentialClass.PAPER` — `CredentialClass` only has one real member, so
    this is how an untrusted adapter payload smuggling a non-PAPER value
    (e.g. through a loosened contract layer upstream) is reproduced here."""

    value = "LIVE"


def test_validate_provenance_rejects_non_paper_credential_class() -> None:
    provenance = AdapterProvenance(
        adapter_type="fake-paper-v1",
        credential_class=CredentialClass.PAPER,
        endpoint_classification="SANDBOX",
        provider_sandbox_account_ref="sandbox-acct-1",
    )
    tampered = replace(provenance, credential_class=_LiveCredentialClass())  # type: ignore[arg-type]
    with pytest.raises(InvalidProvenanceError, match="PAPER"):
        validate_provenance(tampered)


def test_validate_provenance_rejects_empty_adapter_type() -> None:
    provenance = AdapterProvenance(
        adapter_type="   ",
        credential_class=CredentialClass.PAPER,
        endpoint_classification="SANDBOX",
        provider_sandbox_account_ref="sandbox-acct-1",
    )
    with pytest.raises(InvalidProvenanceError, match="adapter_type"):
        validate_provenance(provenance)


def test_validate_provenance_rejects_live_endpoint_classification() -> None:
    provenance = AdapterProvenance(
        adapter_type="fake-paper-v1",
        credential_class=CredentialClass.PAPER,
        endpoint_classification="LIVE_PRODUCTION",
        provider_sandbox_account_ref="sandbox-acct-1",
    )
    with pytest.raises(InvalidProvenanceError, match="LIVE"):
        validate_provenance(provenance)


# ── Negative tests: application-layer state/ownership guards ───────────────


async def test_pause_deployment_rejects_non_running_state() -> None:
    """77 §3 "Pause: fence token increments" only applies to a RUNNING
    deployment — a READY deployment that never started must not be
    pausable."""
    repo = _FakePaperControlRepository()
    tenant_id = uuid4()
    deployment = _make_deployment(tenant_id=tenant_id, state=DeploymentState.READY)
    repo.seed(deployment)

    with pytest.raises(InvalidDeploymentStateError):
        await pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=deployment.id,
            idempotency_key="pause-not-running",
        )


async def test_pause_deployment_rejects_unknown_deployment_id() -> None:
    repo = _FakePaperControlRepository()
    tenant_id = uuid4()
    with pytest.raises(DeploymentNotFoundError):
        await pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=uuid4(),
            idempotency_key="pause-missing",
        )


async def test_pause_deployment_rejects_cross_tenant_access() -> None:
    repo = _FakePaperControlRepository()
    owner_tenant = uuid4()
    deployment = _make_deployment(tenant_id=owner_tenant, state=DeploymentState.RUNNING)
    repo.seed(deployment)

    with pytest.raises(CrossTenantDeploymentAccessError):
        await pause_deployment(
            repo,
            tenant_id=uuid4(),
            actor_subject_id=owner_tenant,
            deployment_id=deployment.id,
            idempotency_key="pause-cross-tenant",
        )


async def test_stop_deployment_rejects_already_stopped() -> None:
    """A STOPPED deployment is terminal (§2) — re-stopping it under a fresh
    idempotency_key must not be treated as a no-op success."""
    repo = _FakePaperControlRepository()
    tenant_id = uuid4()
    deployment = _make_deployment(tenant_id=tenant_id, state=DeploymentState.STOPPED)
    repo.seed(deployment)

    with pytest.raises(InvalidDeploymentStateError):
        await stop_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=deployment.id,
            idempotency_key="stop-already-stopped",
        )


# ── Failure injection ────────────────────────────────────────────────────────


async def test_pause_deployment_propagates_repository_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the storage dependency raises mid-transition (e.g. connection
    dropped), the application layer must not swallow it into a fake success
    — fail-closed per CLAUDE.md §3 default posture. The deployment's state
    must remain untouched (no partial write)."""
    repo = _FakePaperControlRepository()
    tenant_id = uuid4()
    deployment = _make_deployment(tenant_id=tenant_id, state=DeploymentState.RUNNING)
    repo.seed(deployment)

    async def _boom(*_args: object, **_kwargs: object) -> PaperDeployment:
        raise ConnectionError("simulated dependency failure: pool exhausted")

    monkeypatch.setattr(repo, "increment_fence", _boom)

    with pytest.raises(ConnectionError, match="simulated dependency failure"):
        await pause_deployment(
            repo,
            tenant_id=tenant_id,
            actor_subject_id=tenant_id,
            deployment_id=deployment.id,
            idempotency_key="pause-dependency-failure",
        )

    # Untouched — increment_fence never got a chance to mutate the fake repo.
    still_running = await repo.get_deployment(deployment.id)
    assert still_running is not None
    assert still_running.state == DeploymentState.RUNNING
    assert still_running.fence_token == 0


# ── Performance assertion ───────────────────────────────────────────────────


def test_validate_provenance_scales_within_budget() -> None:
    """D2 numeric perf assertion: validating 2000 provenance records must
    stay well under the pure-CPU budget (no I/O in this path) — budget
    picked generously above observed local runtime to avoid flakiness while
    still catching a real algorithmic regression."""
    provenances = [
        AdapterProvenance(
            adapter_type="fake-paper-v1",
            credential_class=CredentialClass.PAPER,
            endpoint_classification="SANDBOX",
            provider_sandbox_account_ref=f"sandbox-acct-{i}",
        )
        for i in range(2000)
    ]

    start = time.perf_counter()
    for provenance in provenances:
        validate_provenance(provenance)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"validating 2000 provenance records took {elapsed:.3f}s (budget: 1.0s)"
