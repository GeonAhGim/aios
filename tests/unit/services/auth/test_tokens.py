"""tokens.py 단위 테스트 — kid 회전, alg(HS256) 고정, refresh 해시.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2, §3.4, §9 PLT-23
DoD("단위: kid 회전·alg 고정").
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

import src.services.auth.tokens as tokens_module
from src.services.auth.tokens import (
    AccessClaims,
    SigningKeyConfigError,
    TokenExpiredError,
    TokenInvalidError,
    TokenIssuer,
    TokenVerifier,
    hash_refresh_token,
)

KEY_V1 = "11" * 32
KEY_V2 = "22" * 32


def _env(active_kid: str, **extra: str) -> dict[str, str]:
    env = {"JWT_SIGNING_KEYS": f"v1:{KEY_V1},v2:{KEY_V2}", "JWT_ACTIVE_KID": active_kid}
    env.update(extra)
    return env


def _issuer(active_kid: str = "v1") -> TokenIssuer:
    return TokenIssuer.from_env(env=_env(active_kid))


def _verifier() -> TokenVerifier:
    return TokenVerifier.from_env(env=_env("v1"))


def test_issue_access_roundtrip_returns_matching_claims() -> None:
    issuer = _issuer("v1")
    verifier = _verifier()
    user_id, tenant_id, session_id = uuid4(), uuid4(), uuid4()

    token = issuer.issue_access(
        user_id=user_id, tenant_id=tenant_id, session_id=session_id, auth_level="PASSWORD"
    )
    claims = verifier.verify(token)

    assert claims.sub == user_id
    assert claims.tid == tenant_id
    assert claims.sid == session_id
    assert claims.auth_level == "PASSWORD"
    assert claims.schema_version == "v1"
    assert claims.exp - claims.iat == 15 * 60


def test_verify_uses_kid_from_header_after_active_kid_rotates() -> None:
    """kid 회전: v1로 발급된 토큰은 active_kid가 v2로 바뀐 뒤에도 검증돼야 한다
    (verifier가 헤더 kid로 키를 고르지, active_kid로 고르지 않으므로)."""
    old_issuer = _issuer("v1")
    token = old_issuer.issue_access(
        user_id=uuid4(), tenant_id=uuid4(), session_id=uuid4(), auth_level="PASSWORD"
    )

    new_issuer = _issuer("v2")
    new_token = new_issuer.issue_access(
        user_id=uuid4(), tenant_id=uuid4(), session_id=uuid4(), auth_level="PASSWORD"
    )

    verifier = _verifier()  # v1, v2 둘 다 가지고 있음
    assert verifier.verify(token).sub is not None
    assert verifier.verify(new_token).sub is not None
    assert jwt.get_unverified_header(token)["kid"] == "v1"
    assert jwt.get_unverified_header(new_token)["kid"] == "v2"


def test_verify_rejects_unknown_kid() -> None:
    issuer = TokenIssuer(keys={"v9": bytes.fromhex(KEY_V1)}, active_kid="v9")
    token = issuer.issue_access(
        user_id=uuid4(), tenant_id=uuid4(), session_id=uuid4(), auth_level="PASSWORD"
    )
    verifier = _verifier()  # v9는 모름
    with pytest.raises(TokenInvalidError):
        verifier.verify(token)


def test_verify_rejects_other_algorithm() -> None:
    """alg 고정(HS256) negative — 같은 키라도 다른 알고리즘 서명은 거부돼야 한다."""
    user_id, tenant_id, session_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    claims = AccessClaims(
        sub=user_id,
        tid=tenant_id,
        sid=session_id,
        jti=uuid4(),
        iat=int(now.timestamp()),
        exp=int((now + timedelta(minutes=15)).timestamp()),
        nbf=int(now.timestamp()),
        auth_level="PASSWORD",
    )
    tampered = jwt.encode(
        claims.model_dump(mode="json"),
        bytes.fromhex(KEY_V1),
        algorithm="HS384",
        headers={"kid": "v1"},
    )
    with pytest.raises(TokenInvalidError):
        _verifier().verify(tampered)


def test_verify_rejects_expired_token() -> None:
    issuer = _issuer("v1")
    past = datetime.now(timezone.utc) - timedelta(minutes=30)
    token = issuer.issue_access(
        user_id=uuid4(), tenant_id=uuid4(), session_id=uuid4(), auth_level="PASSWORD", now=past
    )
    with pytest.raises(TokenExpiredError):
        _verifier().verify(token)


def test_verify_rejects_tampered_signature() -> None:
    issuer = _issuer("v1")
    token = issuer.issue_access(
        user_id=uuid4(), tenant_id=uuid4(), session_id=uuid4(), auth_level="PASSWORD"
    )
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(TokenInvalidError):
        _verifier().verify(tampered)


def test_access_claims_rejects_unknown_extra_field() -> None:
    base = dict(
        sub=uuid4(),
        tid=uuid4(),
        sid=uuid4(),
        jti=uuid4(),
        iat=1,
        exp=2,
        nbf=1,
        auth_level="PASSWORD",
    )
    with pytest.raises(ValidationError):
        AccessClaims(**base, unexpected_field="x")


def test_from_env_missing_signing_keys_raises() -> None:
    with pytest.raises(SigningKeyConfigError):
        TokenIssuer.from_env(env={"JWT_ACTIVE_KID": "v1"})


def test_from_env_active_kid_not_in_keys_raises() -> None:
    with pytest.raises(SigningKeyConfigError):
        TokenIssuer.from_env(env={"JWT_SIGNING_KEYS": f"v1:{KEY_V1}", "JWT_ACTIVE_KID": "v9"})


def test_issue_refresh_returns_plaintext_and_matching_sha256_hash() -> None:
    plaintext, digest = TokenIssuer.issue_refresh()
    assert plaintext != digest
    assert len(digest) == 64
    assert digest == hash_refresh_token(plaintext)


def test_issue_refresh_is_random_each_call() -> None:
    first, _ = TokenIssuer.issue_refresh()
    second, _ = TokenIssuer.issue_refresh()
    assert first != second


def _issue_and_verify_p95_ms(issuer: TokenIssuer, verifier: TokenVerifier, *, n: int) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        token = issuer.issue_access(
            user_id=uuid4(), tenant_id=uuid4(), session_id=uuid4(), auth_level="PASSWORD"
        )
        verifier.verify(token)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


def test_issue_and_verify_p95_under_borrowed_pretrade_gate_budget() -> None:
    """수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 access 토큰
    발급/검증 전용 항목은 없다 — I/O 없는 순수 CPU 연산(로컬 동기 검사 1회)
    이라는 점에서 가장 가까운 유사 항목("사전거래 게이트 p99 5ms")을 자체
    예산으로 차용한다(task-3148 PLT-22 DEEPEN과 동일 차용 근거).
    issue_access+verify 50회 반복 p95를 그 예산 내로 단언한다."""
    issuer = _issuer("v1")
    verifier = _verifier()

    p95_ms = _issue_and_verify_p95_ms(issuer, verifier, n=50)

    assert p95_ms < 5.0


def test_issue_and_verify_budget_gate_fails_on_injected_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    `jwt.encode`에 10ms 인위 지연을 주입해, 같은 측정 로직이 실제로
    AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    original_encode = tokens_module.jwt.encode

    def _slow_encode(*args: object, **kwargs: object) -> str:
        time.sleep(0.01)
        return original_encode(*args, **kwargs)

    monkeypatch.setattr(tokens_module.jwt, "encode", _slow_encode)

    issuer = _issuer("v1")
    verifier = _verifier()
    p95_ms = _issue_and_verify_p95_ms(issuer, verifier, n=10)

    with pytest.raises(AssertionError):
        assert p95_ms < 5.0
