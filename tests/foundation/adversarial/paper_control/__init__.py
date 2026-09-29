"""Paper Execution & Control adversarial 테스트 — 77번 §1(provenance)/§2(상태
전이표) 순수 규칙 함수(`src/foundation/paper_control/domain/rules.py`)에 대한
negative/실패주입 케이스. DB 없이 도는 단위 테스트라 이 패키지 `__init__.py`에
직접 둔다(task-7930 DEEPEN, 원 리프 task-6704)."""

from __future__ import annotations

import time

import pytest

from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CredentialClass,
    DeploymentState,
)
from src.foundation.paper_control.domain.rules import (
    InvalidDeploymentTransitionError,
    InvalidProvenanceError,
    require_transition_allowed,
    validate_provenance,
)


def _paper_provenance(**overrides) -> AdapterProvenance:
    fields = {
        "adapter_type": "fake-paper-v1",
        "credential_class": CredentialClass.PAPER,
        "endpoint_classification": "SANDBOX",
        "provider_sandbox_account_ref": "sandbox-acct-1",
    }
    fields.update(overrides)
    return AdapterProvenance(**fields)


# --- negative tests (77 §2 상태 전이표) --------------------------------------


def test_require_transition_allowed_rejects_recovery_review_to_running():
    """RECOVERY_REVIEW -> RUNNING은 표에 없다 — 자동 복귀를 금지하는 77 §2
    불변식이 전이표 부재만으로 실제 거부까지 이어지는지 검증한다."""
    with pytest.raises(InvalidDeploymentTransitionError):
        require_transition_allowed(DeploymentState.RECOVERY_REVIEW, DeploymentState.RUNNING)


def test_require_transition_allowed_rejects_transition_from_terminal_stopped():
    with pytest.raises(InvalidDeploymentTransitionError):
        require_transition_allowed(DeploymentState.STOPPED, DeploymentState.RUNNING)


def test_require_transition_allowed_rejects_transition_from_terminal_failed():
    with pytest.raises(InvalidDeploymentTransitionError):
        require_transition_allowed(DeploymentState.FAILED, DeploymentState.READY)


# --- negative tests (77 §1 / PAP-002 provenance) -----------------------------


class _FakeLiveCredentialClass:
    """CredentialClass는 현재 PAPER 단일 멤버뿐이라(LIVE는 별도 게이트 이후 도입
    예정) 실제 LIVE 멤버 없이 "PAPER가 아닌 credential_class" 경로를 재현하려면
    같은 `.value` 인터페이스를 가진 대역이 필요하다."""

    value = "LIVE"


def test_validate_provenance_rejects_non_paper_credential_class():
    provenance = AdapterProvenance(
        adapter_type="fake-paper-v1",
        credential_class=_FakeLiveCredentialClass(),
        endpoint_classification="SANDBOX",
        provider_sandbox_account_ref="sandbox-acct-1",
    )
    with pytest.raises(InvalidProvenanceError):
        validate_provenance(provenance)


def test_validate_provenance_rejects_blank_adapter_type():
    with pytest.raises(InvalidProvenanceError):
        validate_provenance(_paper_provenance(adapter_type="   "))


def test_validate_provenance_rejects_blank_provider_sandbox_account_ref():
    with pytest.raises(InvalidProvenanceError):
        validate_provenance(_paper_provenance(provider_sandbox_account_ref=""))


def test_validate_provenance_rejects_case_insensitive_live_endpoint():
    with pytest.raises(InvalidProvenanceError):
        validate_provenance(_paper_provenance(endpoint_classification="Live-Prod"))


# --- failure injection -------------------------------------------------------


def test_require_transition_allowed_propagates_lookup_failure(monkeypatch):
    """전이 허용 여부 조회 자체가 깨지면(예: 상태표 손상) 조용히 통과시키지
    않고 예외를 그대로 전파해야 한다(fail-closed) — 77 §2."""
    import src.foundation.paper_control.domain.rules as rules_module

    def _boom(current, target):
        raise RuntimeError("injected transition table failure")

    monkeypatch.setattr(rules_module, "is_transition_allowed", _boom)

    with pytest.raises(RuntimeError, match="injected transition table failure"):
        rules_module.require_transition_allowed(DeploymentState.READY, DeploymentState.RUNNING)


# --- performance assertion ---------------------------------------------------


@pytest.mark.perf
def test_validate_provenance_p95_latency_budget_for_1000_calls():
    """1,000회 provenance 검증(성공 경로)이 p95 50ms 예산 안에 들어야 한다
    (순수 문자열 비교, I/O 없음 — ADR-2026-09-09-C 성능 예산표 기준 로컬 상한)."""
    provenance = _paper_provenance()

    samples = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(1000):
            validate_provenance(provenance)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[-1]
    assert p95 < 0.05, f"validate_provenance p95={p95:.4f}s exceeds 50ms budget for 1000 calls"
