"""FND-07 Paper Execution & Control 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

import dataclasses

import pytest

from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CredentialClass,
    DeploymentCommand,
    DeploymentState,
    PaperDeployment,
)
from src.foundation.paper_control.domain.rules import (
    InvalidProvenanceError,
    is_transition_allowed,
    validate_provenance,
)
from src.foundation.paper_control.ports.paper_adapter import PaperExecutionAdapter


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


def test_paper_execution_adapter_has_no_credential_or_generic_exchange_method():
    """PAP-009 "API/UI projection cannot forge state or direct-call adapter" —
    이 계약 자체가 provenance 증명 없이는 아무것도 못 하게 좁혀져 있는지
    확인한다(CON-009와 같은 검증 방식). live 주문/자격증명 관련 이름이
    하나라도 있으면 이 포트를 통해 일반 ExchangeAdapter가 몰래 흘러들어올
    여지가 생긴다."""
    forbidden_substrings = ("credential", "apikey", "secret", "withdraw", "transfer")
    methods = [name for name in dir(PaperExecutionAdapter) if not name.startswith("_")]
    assert methods == ["cancel_paper_order", "fetch_paper_state", "submit_paper_intent"]
    for method in methods:
        lowered = method.lower()
        assert not any(f in lowered for f in forbidden_substrings), method


def test_deployment_command_carries_no_secret_field():
    """PAP-010 "trace/audit includes all pinned refs and contains no
    credential/secret" — DeploymentCommand(감사 대상 레코드)에 애초에
    secret류 필드가 없는지 구조적으로 확인한다."""
    forbidden_substrings = ("secret", "credential", "password", "apikey", "token")
    field_names = [f.name.lower() for f in dataclasses.fields(DeploymentCommand)]
    for name in field_names:
        assert not any(f in name for f in forbidden_substrings), name


def test_paper_deployment_provenance_carries_no_secret_field():
    """PAP-010 — provenance(AdapterProvenance)도 마찬가지로 구조화된 근거만
    담고, 원문 자격증명은 담지 않는다(77번 §4 "never a generic/live
    ExchangeAdapter")."""
    forbidden_substrings = ("secret", "credential", "password", "apikey", "token")
    # "credential_class"는 원문 자격증명이 아니라 PAPER/LIVE 분류값이고,
    # "fence_token"은 동시성 방어용 정수 카운터(105번 표준)라 "token"이라는
    # 단어를 우연히 공유할 뿐 인증 토큰이 아니다 — 둘 다 이 검사의 대상이
    # 아니다.
    allowed_field_names = {"credential_class", "fence_token"}
    for model in (AdapterProvenance, PaperDeployment):
        field_names = [f.name.lower() for f in dataclasses.fields(model)]
        for name in field_names:
            if name in allowed_field_names:
                continue
            assert not any(f in name for f in forbidden_substrings), (model.__name__, name)
