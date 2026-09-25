"""Unit tests for `src/foundation/ai/gateway/domain/token_rules.py` -- task-2636
AI-2 DoD ("상승 불가·revoke 즉시·단일 사용")."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.ai.gateway.domain import token_rules as token_rules_module
from src.foundation.ai.gateway.domain.token_rules import (
    AgentToken,
    InstrumentNotAllowedError,
    NotionalCapExceededError,
    Scope,
    ScopeDeniedError,
    ScopeEscalationError,
    TokenExpiredError,
    TokenRevokedError,
    TokenRuleError,
    authorize,
    is_expired,
    is_revoked,
    issue_scopes,
)

_NOW = datetime(2026, 9, 17, 0, 0, 0, tzinfo=timezone.utc)


def _token(
    *,
    scopes: frozenset[Scope] = frozenset({Scope.READ, Scope.RESEARCH}),
    allow_instruments: frozenset[str] = frozenset({"BTC-USDT"}),
    notional_cap: Decimal = Decimal("1000"),
    expires_at: datetime = _NOW + timedelta(hours=1),
    revoked_at: datetime | None = None,
) -> AgentToken:
    return AgentToken(
        token_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        scopes=scopes,
        allow_instruments=allow_instruments,
        notional_cap=notional_cap,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


# --- issue_scopes: 스코프 상승 불가 ---


def test_issue_scopes_returns_requested_when_within_grantable() -> None:
    requested = frozenset({Scope.READ})
    grantable = frozenset({Scope.READ, Scope.RESEARCH})
    assert issue_scopes(requested, grantable) == requested


def test_issue_scopes_rejects_escalation_beyond_grantable() -> None:
    with pytest.raises(ScopeEscalationError):
        issue_scopes(frozenset({Scope.PAPER}), frozenset({Scope.READ}))


def test_issue_scopes_rejects_empty_request() -> None:
    with pytest.raises(TokenRuleError):
        issue_scopes(frozenset(), frozenset({Scope.READ}))


# --- AgentToken 불변조건: paper_only, 최소 1개 스코프 ---


def test_agent_token_rejects_non_paper_only() -> None:
    with pytest.raises(TokenRuleError):
        AgentToken(
            token_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            scopes=frozenset({Scope.READ}),
            allow_instruments=frozenset(),
            notional_cap=Decimal("0"),
            expires_at=_NOW,
            paper_only=False,
        )


def test_agent_token_rejects_empty_scopes() -> None:
    with pytest.raises(TokenRuleError):
        _token(scopes=frozenset())


# --- authorize: revoke 즉시, 만료, 스코프, 종목, 명목가 ---


def test_authorize_passes_when_all_checks_ok() -> None:
    token = _token(scopes=frozenset({Scope.PROPOSE}))
    authorize(
        token,
        Scope.PROPOSE,
        _NOW,
        instrument="BTC-USDT",
        notional=Decimal("500"),
    )


def test_authorize_rejects_revoked_token_immediately_at_revocation_instant() -> None:
    """§2.1 "만료·revoke 즉시 반영" -- revoked_at == now인 그 순간부터 이미
    거부다(유예 없음)."""
    token = _token(revoked_at=_NOW)
    with pytest.raises(TokenRevokedError):
        authorize(token, Scope.READ, _NOW)


def test_authorize_allows_up_to_the_instant_before_revocation() -> None:
    token = _token(revoked_at=_NOW)
    authorize(token, Scope.READ, _NOW - timedelta(microseconds=1))


def test_authorize_rejects_expired_token() -> None:
    token = _token(expires_at=_NOW)
    with pytest.raises(TokenExpiredError):
        authorize(token, Scope.READ, _NOW)


def test_authorize_rejects_ungranted_scope() -> None:
    token = _token(scopes=frozenset({Scope.READ}))
    with pytest.raises(ScopeDeniedError):
        authorize(token, Scope.PAPER, _NOW)


def test_authorize_rejects_disallowed_instrument() -> None:
    token = _token(scopes=frozenset({Scope.PROPOSE}), allow_instruments=frozenset({"BTC-USDT"}))
    with pytest.raises(InstrumentNotAllowedError):
        authorize(token, Scope.PROPOSE, _NOW, instrument="ETH-USDT")


def test_authorize_rejects_notional_over_cap() -> None:
    token = _token(scopes=frozenset({Scope.PAPER}), notional_cap=Decimal("100"))
    with pytest.raises(NotionalCapExceededError):
        authorize(token, Scope.PAPER, _NOW, notional=Decimal("100.01"))


def test_authorize_checks_revocation_before_scope() -> None:
    """검사 순서 계약: 죽은 토큰이 우연히 맞는 스코프를 들고 있어도 "revoke
    됐다"가 먼저 보고돼야 한다(스코프가 맞다는 착각을 주지 않는다)."""
    token = _token(scopes=frozenset({Scope.READ}), revoked_at=_NOW)
    with pytest.raises(TokenRevokedError):
        authorize(token, Scope.PAPER, _NOW)


def test_is_expired_and_is_revoked_are_pure_predicates() -> None:
    token = _token(expires_at=_NOW, revoked_at=_NOW - timedelta(seconds=1))
    assert is_expired(token, _NOW) is True
    assert is_revoked(token, _NOW) is True
    assert is_revoked(token, _NOW - timedelta(seconds=2)) is False


# --- 실패 주입: 상류 데이터 손상이 조용히 통과하지 않고 fail-closed 거부 ---


def test_authorize_rejects_string_contaminated_notional_cap_from_upstream_serialization() -> None:
    """실패 주입: 상류(토큰 저장소 역직렬화)가 손상되어 `notional_cap`이
    Decimal이 아니라 문자열로 섞여 들어오면(CLAUDE.md #3 "Monetary amounts
    are Decimal, never float" 규율이 지키려는 것과 같은 부류의 타입 붕괴 --
    JSON 왕복에서 흔한 실패 양식), Decimal과 str의 순서 비교는 이미
    TypeError를 내므로(부동소수와 달리 str은 암묵적 변환이 없다) 잘못된
    통과/거부를 조용히 내리지 않고 fail-closed 거부해야 한다(dataclass는
    frozen이라 __post_init__ 이후 직접 대입은 object.__setattr__로만 가능 --
    역직렬화 손상을 흉내낸다)."""
    token = _token(scopes=frozenset({Scope.PAPER}))
    object.__setattr__(token, "notional_cap", "100.00")  # 주입된 손상: str, Decimal 아님
    with pytest.raises(TypeError):
        authorize(token, Scope.PAPER, _NOW, notional=Decimal("50"))


def test_authorize_rejects_naive_datetime_expiry_from_upstream_corruption() -> None:
    """실패 주입: 상류가 tz-aware 규율(CLAUDE.md #3 "All datetimes are
    timezone-aware UTC")을 어기고 naive datetime을 `expires_at`에 채우면,
    naive/aware datetime 비교는 이미 TypeError를 내므로 "만료 아님"으로
    조용히 통과시키지 않고 fail-closed 거부해야 한다."""
    token = _token()
    object.__setattr__(token, "expires_at", datetime(2026, 9, 17))  # naive, 주입된 손상
    with pytest.raises(TypeError):
        authorize(token, Scope.READ, _NOW)


# --- 수치 성능 단언: authorize() 핫 패스(모든 MCP 도구의 유일한 인가 지점) ---
# §7 SLO "MCP read 도구 p95 300ms" 예산 아래, authorize() 자체는 그 경로의
# CPU 전용(순수 비교) 구간이므로 훨씬 좁은 자체 예산을 건다.

_AUTHORIZE_BUDGET_MS = 1.0


def _authorize_latencies_ms(iterations: int = 200) -> list[float]:
    token = _token(scopes=frozenset({Scope.PROPOSE}))
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        authorize(token, Scope.PROPOSE, _NOW, instrument="BTC-USDT", notional=Decimal("10"))
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_authorize_p95_latency_within_self_declared_budget() -> None:
    samples = _authorize_latencies_ms()
    p95_ms = _p95(samples)
    print(f"[AI-2 token_rules] authorize p95={p95_ms:.4f}ms budget<{_AUTHORIZE_BUDGET_MS:.1f}ms")
    assert p95_ms < _AUTHORIZE_BUDGET_MS


def test_authorize_budget_gate_actually_fails_past_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """게이트 적색 재현: authorize()가 위임하는 `is_revoked` 경로(호출당
    1회)에 예산을 실제로 넘기는 지연을 주입했을 때 위 성능 단언이 진짜로
    AssertionError를 내는지 확인한다 -- 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_is_revoked = token_rules_module.is_revoked

    def _stalled_is_revoked(token: AgentToken, now: datetime) -> bool:
        time.sleep(_AUTHORIZE_BUDGET_MS / 1000.0)
        return original_is_revoked(token, now)

    monkeypatch.setattr(token_rules_module, "is_revoked", _stalled_is_revoked)

    samples = _authorize_latencies_ms(iterations=5)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _AUTHORIZE_BUDGET_MS
