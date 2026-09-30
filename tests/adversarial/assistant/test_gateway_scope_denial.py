"""U-3a 적대적 테스트 -- 어시스턴트 경로가 쓰는 Agent Gateway 토큰은
PROPOSE 스코프만 가지며, PAPER(실행) 스코프를 절대 통과시키지 않는다.

Spec: ADR-2026-09-05-A "자금 이동·LIVE·리스크 정책 변경 capability는
게이트웨이에 존재하지 않는다"의 어시스턴트 경로 버전 -- 이 경로는 PROPOSE
조차 아니라 그보다 낮은 "생성/설명"만 하지만, 최소한 PAPER 실행 스코프는
구조적으로 거부돼야 한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.ai.gateway.domain import token_rules
from src.foundation.ai.gateway.domain.token_rules import (
    AgentToken,
    Scope,
    ScopeDeniedError,
    TokenExpiredError,
    TokenRevokedError,
    TokenRuleError,
)

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def _propose_only_token() -> AgentToken:
    return AgentToken(
        token_id=uuid4(),
        tenant_id=uuid4(),
        scopes=frozenset({Scope.PROPOSE}),
        allow_instruments=frozenset(),
        notional_cap=Decimal(0),
        expires_at=NOW + timedelta(minutes=5),
    )


def test_propose_only_token_authorizes_propose() -> None:
    token_rules.authorize(_propose_only_token(), Scope.PROPOSE, NOW)  # 예외 없음


@pytest.mark.parametrize("scope", [Scope.PAPER, Scope.READ, Scope.RESEARCH])
def test_propose_only_token_denies_every_other_scope(scope: Scope) -> None:
    with pytest.raises(ScopeDeniedError):
        token_rules.authorize(_propose_only_token(), scope, NOW)


def test_paper_only_false_cannot_be_constructed() -> None:
    """I-06 fail-closed at construction: a PAPER-execution-capable AgentToken
    with paper_only=False must never exist, even transiently."""
    with pytest.raises(TokenRuleError):
        AgentToken(
            token_id=uuid4(),
            tenant_id=uuid4(),
            scopes=frozenset({Scope.PROPOSE}),
            allow_instruments=frozenset(),
            notional_cap=Decimal(0),
            expires_at=NOW + timedelta(minutes=5),
            paper_only=False,
        )


def test_scopeless_token_cannot_be_constructed() -> None:
    with pytest.raises(TokenRuleError):
        AgentToken(
            token_id=uuid4(),
            tenant_id=uuid4(),
            scopes=frozenset(),
            allow_instruments=frozenset(),
            notional_cap=Decimal(0),
            expires_at=NOW + timedelta(minutes=5),
        )


def test_expired_token_denies_even_a_granted_scope() -> None:
    """Liveness is checked before scope membership -- an expired token must
    be denied even though PROPOSE is in its scopes frozenset, so a dead
    token carrying a matching scope never looks "fine"."""
    token = _propose_only_token()
    with pytest.raises(TokenExpiredError):
        token_rules.authorize(token, Scope.PROPOSE, token.expires_at)


def test_revoked_token_denies_even_a_granted_scope() -> None:
    token = AgentToken(
        token_id=uuid4(),
        tenant_id=uuid4(),
        scopes=frozenset({Scope.PROPOSE}),
        allow_instruments=frozenset(),
        notional_cap=Decimal(0),
        expires_at=NOW + timedelta(minutes=5),
        revoked_at=NOW - timedelta(seconds=1),
    )
    with pytest.raises(TokenRevokedError):
        token_rules.authorize(token, Scope.PROPOSE, NOW)


def test_revocation_check_failure_propagates_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패주입: revoke-상태 조회(is_revoked)를 구동하는 의존성이 예외를 던지면
    authorize()는 그 예외를 삼키지 않고 그대로 전파해야 한다 -- 조회 실패를
    "스코프 통과"로 오독하면 PAPER 실행 스코프 거부라는 불변식이 깨진다."""

    def _boom(token: AgentToken, now: datetime) -> bool:
        raise RuntimeError("revocation store unreachable")

    monkeypatch.setattr(token_rules, "is_revoked", _boom)

    with pytest.raises(RuntimeError, match="revocation store unreachable"):
        token_rules.authorize(_propose_only_token(), Scope.PROPOSE, NOW)
