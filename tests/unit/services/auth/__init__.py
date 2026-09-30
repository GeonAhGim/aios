"""DEEPEN -- negative / failure-injection / performance 보강 (task-10183).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2)) -- negative test 0건이던
tests/unit/services/auth/__init__.py를 DEEPEN 기준에 맞춰 보강한다.

대상: src/services/auth/session_repository.py 의 `verify_principal_binding`
(F3, AUDIT_2026-09-30 §auth_rls) -- signature가 유효한 access token의 JWT
claims(sub/tid/auth_level)가 실제 세션 소유자와 일치하는지 검사하는 순수
함수. 기존 tests/integration/auth/test_session_principal_binding.py는 실DB
경로(rotate_refresh/get_active)를 통해서만 간접 호출하고, 이 순수 함수를
직접 겨냥한 경계값 단위 테스트가 없었다 -- DEEPEN 대상으로 적합하다.
`_record_reuse_detected_audit`의 best-effort 예외 스왈로우도 실패주입으로
같이 검증한다.

DoD:
- negative test 3건 이상 (불변식 위반 입력을 명시적으로 거부)
- 실패주입 케이스 1건 이상 (monkeypatch로 의존성 예외 유발)
- `pytest tests/unit/services/auth/__init__.py -q` 통과
- docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.services.auth import session_repository as sr
from src.services.auth.tokens import AuthLevel
from tests._perf.relative_budget import RelativeBudget

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_session(
    *,
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
    auth_level: AuthLevel = "PASSWORD",
) -> sr.Session:
    return sr.Session(
        id=uuid4(),
        user_id=user_id if user_id is not None else uuid4(),
        tenant_id=tenant_id if tenant_id is not None else uuid4(),
        refresh_hash="hash",
        auth_level=auth_level,
        issued_at=_NOW,
        rotated_at=None,
        expires_at=_NOW,
        revoked_at=None,
        revoke_reason=None,
    )


# ── negative tests: mismatched claims must be rejected ─────────────────


def test_verify_principal_binding_rejects_user_id_mismatch() -> None:
    """Negative: a session's `user_id` differing from the JWT `sub` claim
    must raise -- this is the "mixed session + claims" attack the F3 audit
    flagged (session A's sid combined with user B's sub)."""
    session = _make_session()
    with pytest.raises(sr.PrincipalMismatchError):
        sr.verify_principal_binding(
            session,
            user_id=uuid4(),
            tenant_id=session.tenant_id,
            auth_level=session.auth_level,
        )


def test_verify_principal_binding_rejects_tenant_id_mismatch() -> None:
    """Negative: a session's `tenant_id` differing from the JWT `tid` claim
    must raise, independent of user_id matching."""
    session = _make_session()
    with pytest.raises(sr.PrincipalMismatchError):
        sr.verify_principal_binding(
            session,
            user_id=session.user_id,
            tenant_id=uuid4(),
            auth_level=session.auth_level,
        )


def test_verify_principal_binding_rejects_auth_level_mismatch() -> None:
    """Negative: a session's `auth_level` differing from the JWT
    `auth_level` claim must raise -- otherwise an MFA-gated session could be
    presented as PASSWORD-level (or vice versa) without re-verification."""
    session = _make_session(auth_level="MFA_VERIFIED")
    with pytest.raises(sr.PrincipalMismatchError):
        sr.verify_principal_binding(
            session,
            user_id=session.user_id,
            tenant_id=session.tenant_id,
            auth_level="PASSWORD",
        )


def test_verify_principal_binding_error_message_omits_no_identifiers() -> None:
    """Negative: the raised error must name `session_id` so the caller can
    correlate the rejection in logs without re-deriving it -- a bare
    exception with no identifiers would be unauditable."""
    session = _make_session()
    with pytest.raises(sr.PrincipalMismatchError, match=str(session.id)):
        sr.verify_principal_binding(
            session,
            user_id=uuid4(),
            tenant_id=session.tenant_id,
            auth_level=session.auth_level,
        )


# ── positive control: matching claims pass through ─────────────────────


def test_verify_principal_binding_accepts_matching_claims() -> None:
    """Positive control: claims that exactly match the session's owner,
    tenant, and auth level must not raise."""
    session = _make_session(auth_level="MFA_VERIFIED")
    sr.verify_principal_binding(
        session,
        user_id=session.user_id,
        tenant_id=session.tenant_id,
        auth_level="MFA_VERIFIED",
    )


# ── failure-injection: audit-log write failure must not propagate ──────


@pytest.mark.asyncio
async def test_record_reuse_detected_audit_swallows_backend_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-injection: if the audit backend (WORM store) raises while
    recording `auth.refresh_reuse_detected`, `_record_reuse_detected_audit`
    must swallow it rather than propagate -- the session is already revoked
    by the time this runs, so an audit outage must not surface as an error
    that could mask the revoke having already succeeded (fail-closed on the
    revoke, best-effort on the audit trail, per the module docstring)."""

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated audit backend outage")

    monkeypatch.setattr(sr, "record_audit_log", _boom)

    # conn is never touched by the monkeypatched record_audit_log above.
    await sr._record_reuse_detected_audit(None, uuid4(), None)  # type: ignore[arg-type]


# ── numeric performance assertion ───────────────────────────────────────


def test_verify_principal_binding_scales_linearly_within_relative_budget() -> None:
    """Numeric performance assertion: 500,000 calls to
    `verify_principal_binding` with matching claims must stay within a small
    multiple of a fixed CPU calibration loop -- this is three attribute
    comparisons per call, no quadratic blowup expected. The call count is
    large enough to clear Windows' ~15.6ms `time.process_time()` clock-tick
    quantization (see `RelativeBudget`'s calibration-loop docstring) --
    fewer iterations measured as a spurious 0ms op time, ratio 0."""
    session = _make_session(auth_level="MFA_VERIFIED")

    def _run() -> None:
        for _ in range(500_000):
            sr.verify_principal_binding(
                session,
                user_id=session.user_id,
                tenant_id=session.tenant_id,
                auth_level="MFA_VERIFIED",
            )

    budget = RelativeBudget()
    sample = budget.assert_within(
        _run,
        max_ratio=5.0,
        mode="cpu",
        label="session_repository.verify_principal_binding/500000 calls",
    )
    assert sample.ratio > 0
