"""FND-07 Paper Execution & Control 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

import pytest

from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CredentialClass,
    DeploymentState,
)
from src.foundation.paper_control.domain.rules import (
    InvalidProvenanceError,
    is_transition_allowed,
    validate_provenance,
)


def _provenance(**overrides: object) -> AdapterProvenance:
    defaults: dict[str, object] = dict(
        adapter_type="fake-paper-v1",
        credential_class=CredentialClass.PAPER,
        endpoint_classification="SANDBOX",
        provider_sandbox_account_ref="sandbox-acct-1",
    )
    defaults.update(overrides)
    return AdapterProvenance(**defaults)  # type: ignore[arg-type]


def test_validate_provenance_accepts_valid_paper_provenance():
    validate_provenance(_provenance())  # no raise


def test_validate_provenance_rejects_missing_adapter_type():
    with pytest.raises(InvalidProvenanceError):
        validate_provenance(_provenance(adapter_type="  "))


def test_validate_provenance_rejects_missing_sandbox_ref():
    with pytest.raises(InvalidProvenanceError):
        validate_provenance(_provenance(provider_sandbox_account_ref=""))


def test_validate_provenance_rejects_live_looking_endpoint():
    """PAP-002 — live endpoint rejects before adapter call."""
    with pytest.raises(InvalidProvenanceError):
        validate_provenance(_provenance(endpoint_classification="LIVE_PRODUCTION"))


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (DeploymentState.REQUESTED, DeploymentState.READY, True),
        (DeploymentState.REQUESTED, DeploymentState.FAILED, True),
        (DeploymentState.READY, DeploymentState.RUNNING, True),
        (DeploymentState.READY, DeploymentState.STOPPED, True),
        (DeploymentState.RUNNING, DeploymentState.PAUSED, True),
        (DeploymentState.RUNNING, DeploymentState.STOPPED, True),
        (DeploymentState.PAUSED, DeploymentState.RUNNING, True),
        (DeploymentState.PAUSED, DeploymentState.STOPPED, True),
        (DeploymentState.DEGRADED, DeploymentState.RECOVERY_REVIEW, True),
        # 77번 §2 "RECOVERY_REVIEW cannot transition to RUNNING automatically"
        (DeploymentState.RECOVERY_REVIEW, DeploymentState.RUNNING, False),
        (DeploymentState.RECOVERY_REVIEW, DeploymentState.STOPPED, True),
        (DeploymentState.REQUESTED, DeploymentState.RUNNING, False),
        (DeploymentState.STOPPED, DeploymentState.RUNNING, False),
        (DeploymentState.FAILED, DeploymentState.READY, False),
    ],
)
def test_is_transition_allowed_matches_state_table(current, target, allowed):
    assert is_transition_allowed(current, target) is allowed
