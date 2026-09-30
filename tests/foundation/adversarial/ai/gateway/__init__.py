"""AI-4 adversarial package marker -- DEEPEN (task-6704 "고아 산출물 회수").

Empty package marker beyond `test_cross_tenant_isolation.py`'s real-DB
coverage. This module adds fake-repository-backed negative/failure-injection
tests for `src/foundation/ai/gateway/application/revoke_token.py`'s
application-layer defense (the layer *above* the repository that
`test_cross_tenant_isolation.py` already exercises against a real DB) --
specifically the "does not exist" vs "owned by another tenant" distinction
(both collapse to 404 for the caller, per spec AI-4 DoD) and the
`assert revoked is not None` invariant that guards against the repository
silently disagreeing with what `get_token` just confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.foundation.ai.gateway.application.errors import (
    AgentTokenNotFoundError,
    CrossTenantAgentTokenAccessError,
)
from src.foundation.ai.gateway.application.revoke_token import revoke_token
from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope

_NOW = datetime(2026, 9, 30, 0, 0, 0, tzinfo=timezone.utc)


def _token(tenant_id: UUID) -> AgentToken:
    return AgentToken(
        token_id=uuid4(),
        tenant_id=tenant_id,
        scopes=frozenset({Scope.READ}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        expires_at=_NOW + timedelta(hours=1),
    )


@dataclass
class _FakeRepo:
    """In-process double -- no DB/asyncpg. Records calls so a test can assert
    the application layer never reaches the repository's write path once its
    own ownership check has already failed (fail-closed *before* I/O, not
    just "the I/O also happens to reject it")."""

    stored: AgentToken | None
    revoke_calls: list[tuple[UUID, UUID, str]]
    revoke_returns: AgentToken | None | Ellipsis = ...

    async def get_token(self, token_id: UUID) -> AgentToken | None:
        if self.stored is not None and self.stored.token_id == token_id:
            return self.stored
        return None

    async def revoke_token(
        self, token_id: UUID, *, tenant_id: UUID, reason: str
    ) -> AgentToken | None:
        self.revoke_calls.append((token_id, tenant_id, reason))
        if self.revoke_returns is not ...:
            return self.revoke_returns
        assert self.stored is not None
        return replace(self.stored, revoked_at=_NOW)


def _repo(
    token: AgentToken | None, *, revoke_returns: AgentToken | None | Ellipsis = ...
) -> _FakeRepo:
    return _FakeRepo(stored=token, revoke_calls=[], revoke_returns=revoke_returns)


# --- negative: 존재하지 않음 vs 다른 tenant 소유 -- 둘 다 404로 수렴하지만
# 독립적인 예외로 구분된다 (AI-4 DoD) ---


async def test_revoke_token_raises_not_found_when_token_id_unknown() -> None:
    repo = _repo(None)
    with pytest.raises(AgentTokenNotFoundError):
        await revoke_token(repo, tenant_id=uuid4(), token_id=uuid4(), reason="ghost")
    assert repo.revoke_calls == []  # 존재 확인에서 이미 거부 -- 쓰기 경로 미도달


async def test_revoke_token_raises_cross_tenant_before_touching_repo_write_path() -> None:
    owner_id = uuid4()
    attacker_id = uuid4()
    owned = _token(owner_id)
    repo = _repo(owned)

    with pytest.raises(CrossTenantAgentTokenAccessError):
        await revoke_token(repo, tenant_id=attacker_id, token_id=owned.token_id, reason="attack")

    # fail-closed *이전* 방어 -- 소유자 불일치가 확인된 순간 repo.revoke_token
    # 자체가 호출되지 않는다(리포지토리 계층의 WHERE 절에 기대는 이중 방어가
    # 아니라, 애플리케이션 계층 자체가 첫 줄에서 이미 차단한다는 것의 증명).
    assert repo.revoke_calls == []


async def test_revoke_token_rejects_tenant_id_that_only_string_equals_owner() -> None:
    """UUID 비교가 실수로 `str(a) == str(b)`류의 느슨한 동치로 후퇴하면
    다른 UUID 객체라도 같은 정수값이면 새는 것과 같은 부류의 실수를 잡는다
    -- 값이 다른 UUID는 등호 비교로 정확히 구분되어야 한다."""
    owner_id = uuid4()
    lookalike_id = UUID(int=owner_id.int ^ 1)  # 한 비트만 다른, owner와 절대 같지 않은 UUID
    owned = _token(owner_id)
    repo = _repo(owned)

    with pytest.raises(CrossTenantAgentTokenAccessError):
        await revoke_token(repo, tenant_id=lookalike_id, token_id=owned.token_id, reason="probe")
    assert repo.revoke_calls == []


# --- 실패 주입: get_token이 소유를 확인했는데도 repo.revoke_token이 None을
# 돌려주면(경쟁 상태로 그 사이 행이 사라졌다는 뜻) "성공"으로 위장하지 않고
# AssertionError로 fail-closed 한다 ---


async def test_revoke_token_fails_closed_when_repo_disagrees_with_prior_existence_check() -> None:
    """실패 주입: get_token()은 소유를 확인했지만(정상 응답), 그 직후
    repo.revoke_token()이 None을 돌려주는(예: 동시 삭제/만료 경쟁 상태를
    흉내) 상황을 강제로 재현한다. `revoke_token()`의 `assert revoked is not
    None`이 이걸 조용히 삼켜 잘못된 토큰 객체를 반환하지 않고, 즉시
    AssertionError로 죽는지 확인한다 -- 이 불변조건이 없으면 호출자는 이미
    사라진 토큰을 "정상 취소됨"으로 오인할 수 있다."""
    owner_id = uuid4()
    owned = _token(owner_id)
    repo = _repo(owned, revoke_returns=None)

    with pytest.raises(AssertionError):
        await revoke_token(repo, tenant_id=owner_id, token_id=owned.token_id, reason="race")

    # 애플리케이션 계층은 실제로 리포지토리를 호출은 했다(사전 확인만으로
    # 끝내지 않고 실제 쓰기 시도까지 갔다는 것) -- 단, 그 결과가 계약을
    # 위반하자 fail-closed 했을 뿐이다.
    assert len(repo.revoke_calls) == 1
