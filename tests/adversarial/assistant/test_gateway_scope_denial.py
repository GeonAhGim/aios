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
from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope, ScopeDeniedError

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
