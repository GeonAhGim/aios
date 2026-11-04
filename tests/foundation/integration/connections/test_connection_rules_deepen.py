"""Connected Asset pure-rule 증빙 (DB 불필요) — DEEPEN task-21922.

conftest.py는 픽스처 헬퍼만 담으므로, 불변식 위반 거부(negative)·실패 주입·성능 단언은
이 sibling 파일에 둔다(CLAUDE.md §6-12: near-threshold 파일에 증빙 추가 금지).
"""

from __future__ import annotations

import time

import pytest

from src.foundation.connections.domain import rules
from src.foundation.connections.domain.models import CapabilityScope, ConnectionState
from src.foundation.connections.domain.rules import (
    ForbiddenCapabilityScopeError,
    InvalidConnectionTransitionError,
    compute_scope_fingerprint,
    require_transition_allowed,
    validate_capability_profile,
)
from tests.support.perf_budget import PerfBudget

# --- negative: 불변식 위반 입력은 명시적으로 거부 (74 §1 hard rejection) ---------------


def test_empty_capability_profile_is_rejected() -> None:
    with pytest.raises(ForbiddenCapabilityScopeError) as exc:
        validate_capability_profile([])
    assert exc.value.rejected == ["<empty>"]


def test_trade_scope_rejects_whole_profile_without_partial_acceptance() -> None:
    # 허용 스코프가 섞여 있어도 부분 수락하지 않는다.
    with pytest.raises(ForbiddenCapabilityScopeError) as exc:
        validate_capability_profile(["READ_BALANCE", "TRADE_SPOT"])
    assert exc.value.rejected == ["TRADE_SPOT"]


def test_revoked_connection_cannot_reactivate() -> None:
    with pytest.raises(InvalidConnectionTransitionError):
        require_transition_allowed(ConnectionState.REVOKED, ConnectionState.ACTIVE_READONLY)


def test_pending_consent_cannot_skip_to_active() -> None:
    with pytest.raises(InvalidConnectionTransitionError):
        require_transition_allowed(ConnectionState.PENDING_CONSENT, ConnectionState.ACTIVE_READONLY)


# --- failure injection: 의존성 예외가 fail-closed로 전파된다 ----------------------------


def test_fingerprint_hash_failure_propagates_instead_of_reporting_no_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken_sha256(_data: bytes) -> None:
        raise RuntimeError("hash backend down")

    monkeypatch.setattr(rules.hashlib, "sha256", _broken_sha256)
    scopes = (CapabilityScope.READ_BALANCE,)
    with pytest.raises(RuntimeError, match="hash backend down"):
        rules.detect_scope_drift(scopes, scopes)


# --- performance: perf_budget 픽스처 경유 예산 단언 -----------------------------------


@pytest.mark.perf
def test_scope_fingerprint_within_budget(perf_budget: PerfBudget) -> None:
    scopes = (
        CapabilityScope.READ_BALANCE,
        CapabilityScope.READ_POSITION,
        CapabilityScope.READ_ACTIVITY,
    )
    perf_budget.assert_within(
        lambda: compute_scope_fingerprint(scopes),
        budget_ms=5.0,
        label="compute_scope_fingerprint",
    )


# --- gate-red reproduction: 지연 주입 시 동일 단언이 적색으로 전환됨 -------------------


@pytest.mark.perf
def test_scope_fingerprint_budget_catches_delay_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injecting a 50ms delay into compute_scope_fingerprint must turn the same
    5ms budget assertion red, proving the gate can actually fail."""
    real = rules.compute_scope_fingerprint

    def _slow(scopes: tuple[CapabilityScope, ...]) -> str:
        # Busy-wait: the budget measures CPU time, which sleep() would not consume.
        end = time.process_time() + 0.05
        while time.process_time() < end:
            pass
        return real(scopes)

    monkeypatch.setattr(rules, "compute_scope_fingerprint", _slow)
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: rules.compute_scope_fingerprint((CapabilityScope.READ_BALANCE,)),
            budget_ms=5.0,
            label="compute_scope_fingerprint",
        )
