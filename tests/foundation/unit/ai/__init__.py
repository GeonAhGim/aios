"""tests/foundation/unit/ai/__init__.py — gateway application 계층
(authorize/issue_token/revoke_token) 부정/실패주입/성능 테스트 (fake repo, no DB).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 — negative>=3, failure-injection>=1, perf assertion>=1

이 디렉터리의 `test_token_rules.py`/`test_confirm.py`는 domain 계층
(`gateway/domain/token_rules.py`, `gateway/domain/confirm.py`)의 순수 규칙을
이미 두껍게 덮고, `tests/foundation/integration/ai/gateway/*`와
`tests/foundation/adversarial/ai/gateway/test_cross_tenant_isolation.py`는
`application/{authorize,issue_token,revoke_token}.py`를 실DB
(TEST_DATABASE_URL)로 검증한다. 남은 공백은 그 application 계층 자체를
fake repo(실DB 없이, 의존성 I/O를 흉내내는 인메모리 더블)로 단위 검증하는
것 — "저장소 호출 여부/횟수"까지 단언할 수 있는 층은 fake뿐이다(실DB 경로는
결과만 보이고 중간 호출 횟수는 안 보인다). 이 파일은 그 공백을 메운다.

불변식 참조:
- `authorize.authorize`: 해시로 못 찾은 토큰은 전용 에러 코드가 없어 revoke된
  토큰과 동일하게 401(`TokenRevokedError`)로 fail-closed 수렴한다(모듈
  docstring, spec §3 에러 카탈로그).
- `revoke_token.revoke_token`: `get_token`(테넌트 필터 없음)으로 "존재하지
  않음"과 "다른 테넌트 소유"를 구분해 서로 다른 예외로 올리되, 둘 다 호출측엔
  404로 수렴한다 — 그리고 그 구분이 끝나기 전엔 `repo.revoke_token`(실제
  UPDATE)에 절대 도달하지 않는다.
- `issue_token.issue_token`: `issue_scopes`가 거부하는 요청(빈 스코프 등)은
  `repo.insert_token`(실제 INSERT) 호출 전에 걸러진다 — 컴파일 실패 후
  un-runnable 상태로 저장되는 경우가 없어야 한다는 screener의 `save_screen`과
  같은 클래스의 불변식.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.foundation.ai.gateway.application.authorize import authorize
from src.foundation.ai.gateway.application.errors import (
    AgentTokenNotFoundError,
    CrossTenantAgentTokenAccessError,
)
from src.foundation.ai.gateway.application.issue_token import issue_token
from src.foundation.ai.gateway.application.revoke_token import revoke_token
from src.foundation.ai.gateway.domain.token_rules import (
    AgentToken,
    Scope,
    TokenRevokedError,
    TokenRuleError,
)

_NOW = datetime(2026, 9, 17, 0, 0, 0, tzinfo=timezone.utc)


@dataclass
class FakeAgentTokenRepository:
    """`PostgresAgentTokenRepository`와 같은 메서드 시그니처를 흉내내는
    인메모리 더블 — 호출 여부/횟수를 단언 가능하게 만든다(screener의
    `FakeSavedScreenerRepository`와 같은 역할)."""

    by_hash: dict[str, tuple[AgentToken, datetime]] = field(default_factory=dict)
    by_id: dict[UUID, AgentToken] = field(default_factory=dict)
    get_by_hash_calls: int = 0
    insert_token_calls: int = 0
    get_token_calls: int = 0
    revoke_token_calls: int = 0
    raise_on_get_by_hash: Exception | None = None
    revoke_token_returns_none: bool = False

    async def get_by_hash(self, token_hash: str) -> tuple[AgentToken, datetime] | None:
        self.get_by_hash_calls += 1
        if self.raise_on_get_by_hash is not None:
            raise self.raise_on_get_by_hash
        return self.by_hash.get(token_hash)

    async def get_token(self, token_id: UUID) -> AgentToken | None:
        self.get_token_calls += 1
        return self.by_id.get(token_id)

    async def insert_token(
        self,
        *,
        tenant_id: UUID,
        token_hash: str,
        scopes: frozenset[Scope],
        allow_instruments: frozenset[str],
        notional_cap: Decimal,
        expires_at: datetime,
    ) -> AgentToken:
        self.insert_token_calls += 1
        token = AgentToken(
            token_id=uuid4(),
            tenant_id=tenant_id,
            scopes=scopes,
            allow_instruments=allow_instruments,
            notional_cap=notional_cap,
            expires_at=expires_at,
        )
        self.by_id[token.token_id] = token
        self.by_hash[token_hash] = (token, _NOW)
        return token

    async def revoke_token(
        self, token_id: UUID, *, tenant_id: UUID, reason: str
    ) -> AgentToken | None:
        self.revoke_token_calls += 1
        if self.revoke_token_returns_none:
            return None
        existing = self.by_id.get(token_id)
        if existing is None or existing.tenant_id != tenant_id:
            return None
        revoked = AgentToken(
            token_id=existing.token_id,
            tenant_id=existing.tenant_id,
            scopes=existing.scopes,
            allow_instruments=existing.allow_instruments,
            notional_cap=existing.notional_cap,
            expires_at=existing.expires_at,
            revoked_at=_NOW,
        )
        self.by_id[token_id] = revoked
        return revoked


def _seed_token(
    repo: FakeAgentTokenRepository,
    *,
    tenant_id: UUID,
    scopes: frozenset[Scope] = frozenset({Scope.READ}),
) -> AgentToken:
    token = AgentToken(
        token_id=uuid4(),
        tenant_id=tenant_id,
        scopes=scopes,
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        expires_at=_NOW + timedelta(hours=1),
    )
    repo.by_id[token.token_id] = token
    return token


# ──────────────────────────────────────────────────────────────────────
# 1. Negative tests — 불변식 위반 입력을 명시적으로 거부
# ──────────────────────────────────────────────────────────────────────


class TestNegativeAuthorizeUnknownSecret:
    async def test_negative_unknown_token_secret_raises_token_revoked_not_found(self) -> None:
        """부정: 해시로 매칭되는 토큰이 없으면(존재하지 않거나 secret이 틀림)
        전용 404류 예외가 아니라 TokenRevokedError(401)로 fail-closed 수렴한다
        (authorize.py 모듈 docstring — "no dedicated error code")."""
        repo = FakeAgentTokenRepository()

        with pytest.raises(TokenRevokedError):
            await authorize(repo, token_secret="nonexistent-secret", scope=Scope.READ)

        assert repo.get_by_hash_calls == 1


class TestNegativeIssueTokenRejectsEmptyScopeBeforePersist:
    async def test_negative_empty_scope_request_never_reaches_insert(self) -> None:
        """부정: 빈 스코프 요청은 issue_scopes가 TokenRuleError로 거부하며,
        repo.insert_token(실제 INSERT)은 단 한 번도 호출되지 않는다 —
        컴파일 실패 후 un-runnable 상태로 저장되는 경우가 없어야 한다는
        save_screen과 같은 클래스의 불변식."""
        repo = FakeAgentTokenRepository()

        with pytest.raises(TokenRuleError):
            await issue_token(
                repo,
                tenant_id=uuid4(),
                scopes=frozenset(),
                allow_instruments=frozenset(),
                notional_cap=Decimal("0"),
                ttl=timedelta(hours=1),
                now=_NOW,
            )

        assert repo.insert_token_calls == 0


class TestNegativeRevokeTokenNotFound:
    async def test_negative_unknown_token_id_raises_not_found_without_touching_revoke(
        self,
    ) -> None:
        """부정: 존재하지 않는 token_id는 AgentTokenNotFoundError이며,
        repo.revoke_token(실제 UPDATE)는 호출되지 않는다."""
        repo = FakeAgentTokenRepository()

        with pytest.raises(AgentTokenNotFoundError):
            await revoke_token(repo, tenant_id=uuid4(), token_id=uuid4(), reason="test")

        assert repo.revoke_token_calls == 0


class TestNegativeRevokeTokenCrossTenant:
    async def test_negative_cross_tenant_revoke_raises_without_touching_revoke(self) -> None:
        """부정: 다른 테넌트 소유 토큰의 revoke 시도는 존재를 드러내지 않고
        CrossTenantAgentTokenAccessError이며, repo.revoke_token은 호출되지
        않는다(존재 자체를 드러내지 않는다는 § 4 컨벤션, screener의
        share_screen과 같은 형태)."""
        repo = FakeAgentTokenRepository()
        owner, stranger = uuid4(), uuid4()
        token = _seed_token(repo, tenant_id=owner)

        with pytest.raises(CrossTenantAgentTokenAccessError):
            await revoke_token(repo, tenant_id=stranger, token_id=token.token_id, reason="x")

        assert repo.revoke_token_calls == 0


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection tests — 의존성/경계 예외 유발
# ──────────────────────────────────────────────────────────────────────


class TestFailureInjectionAuthorizeRepoOutage:
    async def test_failure_injection_repo_get_by_hash_crash_propagates(self) -> None:
        """실패주입: repo.get_by_hash가 (토큰 없음이 아니라) RuntimeError로
        크래시하면 authorize()는 이를 삼키지 않고 그대로 전파한다
        (fail-closed 기본 자세, CLAUDE.md §3)."""
        repo = FakeAgentTokenRepository(raise_on_get_by_hash=RuntimeError("db outage"))

        with pytest.raises(RuntimeError, match="db outage"):
            await authorize(repo, token_secret="whatever", scope=Scope.READ)


class TestFailureInjectionRevokeTokenContractViolation:
    async def test_failure_injection_repo_returning_none_after_ownership_confirmed_trips_assert(
        self,
    ) -> None:
        """실패주입: get_token이 존재/소유권을 이미 확인했는데도 repo가
        revoke_token에서 계약을 어기고 None을 반환하면(저장소 구현 버그를
        흉내냄), revoke_token.py의 방어적 assert가 이를 조용히 통과시키지
        않고 AssertionError로 즉시 드러낸다 — "정상 경로에서 None이 나올 수
        없다"는 모듈 docstring의 전제가 실제로 지켜지는지 증명한다(2계층
        방어의 두 번째 층이 실제로 동작함을 보이는 게이트-red 재현)."""
        repo = FakeAgentTokenRepository(revoke_token_returns_none=True)
        tenant = uuid4()
        token = _seed_token(repo, tenant_id=tenant)

        with pytest.raises(AssertionError):
            await revoke_token(repo, tenant_id=tenant, token_id=token.token_id, reason="x")

        assert repo.revoke_token_calls == 1


# ──────────────────────────────────────────────────────────────────────
# 3. Performance assertion
# ──────────────────────────────────────────────────────────────────────

_AUTHORIZE_APPLICATION_BUDGET_MS = 5.0


class TestPerformanceAuthorizeApplicationLayer:
    @pytest.mark.perf
    async def test_perf_authorize_full_application_path_within_budget(self) -> None:
        """성능단언: authorize()는 모든 MCP 도구의 유일한 인가 지점(spec §2.1)
        이므로 hash+fake-repo-lookup+순수 판정을 합친 전체 경로에도 예산을
        건다. `test_token_rules.py`의 순수 판정 전용 예산(1ms)보다 해시
        계산+repo 왕복만큼 느슨하게(5ms) 잡는다."""
        repo = FakeAgentTokenRepository()
        tenant = uuid4()
        secret = "s" * 64
        from src.foundation.ai.gateway.application.issue_token import hash_token_secret

        token = AgentToken(
            token_id=uuid4(),
            tenant_id=tenant,
            scopes=frozenset({Scope.PROPOSE}),
            allow_instruments=frozenset(),
            notional_cap=Decimal("1000"),
            expires_at=_NOW + timedelta(hours=1),
        )
        repo.by_hash[hash_token_secret(secret)] = (token, _NOW)

        samples: list[float] = []
        for _ in range(200):
            started = time.perf_counter()
            await authorize(repo, token_secret=secret, scope=Scope.PROPOSE)
            samples.append((time.perf_counter() - started) * 1000)
        samples.sort()
        p95_ms = samples[min(int(len(samples) * 0.95), len(samples) - 1)]
        print(
            f"[AI-4 authorize] application-layer p95={p95_ms:.4f}ms "
            f"budget<{_AUTHORIZE_APPLICATION_BUDGET_MS:.1f}ms"
        )
        assert p95_ms < _AUTHORIZE_APPLICATION_BUDGET_MS
