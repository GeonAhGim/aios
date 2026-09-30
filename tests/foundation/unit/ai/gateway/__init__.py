"""tests/foundation/unit/ai/gateway/__init__.py -- ai/gateway 패키지 경계 통합/부정/실패주입 테스트

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 -- negative>=3, failure-injection>=1, perf assertion>=1

`contracts/v1.py`(pydantic AgentToken/ConfirmTicket, I/O 없음) ->
`domain/token_rules.py`/`domain/confirm.py`(pure dataclass + 규칙 함수) 경계를
가로지르는 테스트. 각 모듈 자체의 단위 테스트는 이미 test_contracts_v1.py,
tests/foundation/unit/ai/test_token_rules.py, tests/foundation/unit/ai/test_confirm.py에
있으므로, 여기서는 "contract 계층에서 통과한 값이 domain 계층으로 넘어갈 때도
여전히 불변조건을 지키는가"라는 경계 자체의 계약에 집중한다 -- contracts/v1.py의
docstring이 명시하듯 domain 모듈은 contracts를 참조하지 않는 단방향 계층이라,
그 경계에서 조합(변환) 코드는 어느 쪽 단위 테스트에도 안 잡힌다.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.foundation.ai.gateway.application.issue_token import hash_token_secret
from src.foundation.ai.gateway.contracts import v1
from src.foundation.ai.gateway.domain import confirm as confirm_module
from src.foundation.ai.gateway.domain import token_rules
from tests.conftest import PerfBudget

_NOW = datetime(2026, 9, 17, 0, 0, 0, tzinfo=timezone.utc)


def _contract_token(**overrides: Any) -> v1.AgentToken:
    base: dict[str, Any] = dict(
        token_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        scopes=frozenset({v1.Scope.READ, v1.Scope.RESEARCH}),
        allow_instruments=frozenset({"BTC/USDT"}),
        notional_cap=Decimal("10000"),
        expires_at=_NOW,
    )
    base.update(overrides)
    return v1.AgentToken(**base)


def _domain_token_from_contract(
    contract_token: v1.AgentToken, **overrides: Any
) -> token_rules.AgentToken:
    """contracts/v1.py -> domain/token_rules.py 경계 변환 -- AI-17(라우터, 미구현)이
    영속화된 wire 스키마를 pure 도메인 값으로 바꿀 때 수행할 매핑을 흉내낸다.
    `v1.Scope`와 `token_rules.Scope`는 값이 같은 별도 Enum이라 `.value`로 한 번
    풀어서 다시 감싸야 한다 -- 이 재감쌈을 생략하는 실수를 아래 테스트들이 잡는다."""
    kwargs: dict[str, Any] = dict(
        token_id=contract_token.token_id,
        tenant_id=contract_token.tenant_id,
        scopes=frozenset(token_rules.Scope(s.value) for s in contract_token.scopes),
        allow_instruments=contract_token.allow_instruments,
        notional_cap=contract_token.notional_cap,
        expires_at=contract_token.expires_at,
        paper_only=contract_token.paper_only,
    )
    kwargs.update(overrides)
    return token_rules.AgentToken(**kwargs)


# ---------------------------------------------------------------------------
# negative (>=3) -- contract 계층 통과가 domain 계층의 거부를 면제해주지 않는다
# ---------------------------------------------------------------------------


def test_negative_contract_permits_paper_only_false_but_domain_conversion_still_rejects() -> None:
    """부정: `v1.AgentToken`은 `paper_only=False`를 명시적으로 넘기면 계약 계층에서
    그냥 구성된다 (필드에 `default`만 있고 값을 강제하는 validator가 없다) -- 즉
    "wire 스키마가 받아줬으니 안전하다"는 가정이 성립하지 않는다. AI-2의
    `AgentToken.__post_init__`이 이 경계에서 실제 fail-closed 방어선이어야 한다."""
    contract_token = _contract_token(paper_only=False)
    assert contract_token.paper_only is False  # contract 계층은 통과시켰다

    with pytest.raises(token_rules.TokenRuleError, match="paper_only"):
        _domain_token_from_contract(contract_token)


def test_negative_issue_scopes_rejects_escalation_sourced_from_contract_scopes() -> None:
    """부정: contract에서 구성 가능한 스코프 조합(READ+RESEARCH+PROPOSE) 전체가
    발급자의 실제 grantable 집합(READ만)을 넘으면, 그 값이 이미 contract 계층의
    `frozenset[Scope]` 검증을 통과했어도 `issue_scopes`는 여전히 거부해야 한다."""
    contract_token = _contract_token(
        scopes=frozenset({v1.Scope.READ, v1.Scope.RESEARCH, v1.Scope.PROPOSE})
    )
    requested = frozenset(token_rules.Scope(s.value) for s in contract_token.scopes)
    grantable = frozenset({token_rules.Scope.READ})

    with pytest.raises(token_rules.ScopeEscalationError) as exc_info:
        token_rules.issue_scopes(requested, grantable)
    assert token_rules.Scope.RESEARCH in exc_info.value.requested - exc_info.value.grantable


def test_negative_confirm_domain_rejects_digest_mismatch_when_both_valid_at_contract_layer() -> (
    None
):
    """부정: 두 개의 `action_digest`가 각각 독립적으로는 contract 계층의
    sha256-hex 정규식을 통과하는 유효한 값이라도, 하나는 preview 티켓 발급 시
    사용하고 다른 하나를 execute 시점에 제출하면 도메인 계층에서 불일치로
    거부돼야 한다 -- "형식이 유효하다"가 "같은 행위를 가리킨다"를 보장하지 않는다."""
    preview_digest = "a" * 64
    execute_digest = "b" * 64
    preview = v1.ConfirmTicket(
        ticket_id=uuid.uuid4(), action_digest=preview_digest, expires_at=_NOW
    )
    execute = v1.ConfirmTicket(
        ticket_id=uuid.uuid4(), action_digest=execute_digest, expires_at=_NOW
    )
    assert preview.action_digest != execute.action_digest  # 둘 다 contract 검증은 통과했다

    domain_ticket = confirm_module.ConfirmTicket(
        ticket_id=preview.ticket_id,
        action_digest=preview.action_digest,
        expires_at=preview.expires_at,
    )
    with pytest.raises(confirm_module.ConfirmDigestMismatchError):
        confirm_module.verify_and_consume(domain_ticket, execute.action_digest, _NOW)


def test_negative_authorize_rejects_instrument_outside_contract_snapshotted_allowlist() -> None:
    """부정: contract에서 구성한 `allow_instruments` 스냅샷이 domain 계층으로
    그대로 전달돼도, 그 집합 밖의 instrument는 여전히 거부된다 -- 변환 과정에서
    허용 목록이 조용히 전체 허용(빈 집합처럼 취급)으로 바뀌는 회귀를 잡는다."""
    contract_token = _contract_token(allow_instruments=frozenset({"BTC/USDT"}))
    domain_token = _domain_token_from_contract(contract_token)

    with pytest.raises(token_rules.InstrumentNotAllowedError):
        token_rules.authorize(
            domain_token,
            token_rules.Scope.READ,
            _NOW,
            instrument="ETH/USDT",
        )


# ---------------------------------------------------------------------------
# failure-injection (>=1)
# ---------------------------------------------------------------------------


def test_failure_injection_hashing_dependency_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: `hash_token_secret`이 위임하는 `sha256_hex`가 예상 밖 예외를
    던지면 (예: 의존성 패치/라이브러리 장애) 그 예외가 그대로 전파돼야 한다 --
    조용히 삼켜 빈 문자열이나 고정 해시로 "발급 성공"을 위장해서는 안 된다
    (issue_token.py의 fail-closed 전제: 이 해시만이 DB에 영속화되는 유일한
    토큰 식별자다)."""
    import src.foundation.ai.gateway.application.issue_token as issue_token_module

    def _boom(data: bytes) -> str:
        raise RuntimeError("injected sha256 dependency failure")

    monkeypatch.setattr(issue_token_module, "sha256_hex", _boom)

    with pytest.raises(RuntimeError, match="injected sha256 dependency failure"):
        hash_token_secret("some-opaque-secret")


# ---------------------------------------------------------------------------
# 성능 단언 (>=1)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_bulk_contract_to_domain_conversion_and_authorize_meets_latency_budget(
    perf_budget: PerfBudget,
) -> None:
    """MCP 도구 호출마다 authorize()가 도달하기 전에 contract->domain 변환이
    선행되는 핫 패스다 -- 5,000건 변환+인가 판단이 절대시간 예산 내여야 건당
    상수시간에서 벗어나지 않았다고 볼 수 있다."""
    n = 5_000
    budget_ms = 2500.0  # 실측 로컬 <700ms
    contract_tokens = [_contract_token(token_id=uuid.uuid4()) for _ in range(n)]

    def _run() -> None:
        for contract_token in contract_tokens:
            domain_token = _domain_token_from_contract(contract_token)
            token_rules.authorize(domain_token, token_rules.Scope.READ, _NOW)

    sample = perf_budget.assert_within(_run, budget_ms=budget_ms, label="[AI gateway __init__]")
    desc = perf_budget.describe(sample, budget_ms=budget_ms)
    print(f"[AI gateway __init__] {n}건 convert+authorize in {desc}")


@pytest.mark.perf
def test_gate_red_budget_actually_fails_past_budget() -> None:
    """위 성능 단언이 실제로 예산 초과를 잡아내는지(tautology 아님) 확인한다."""
    n = 200
    absurdly_low_budget_sec = 1e-9
    contract_tokens = [_contract_token(token_id=uuid.uuid4()) for _ in range(n)]

    start = time.perf_counter()
    for contract_token in contract_tokens:
        domain_token = _domain_token_from_contract(contract_token)
        token_rules.authorize(domain_token, token_rules.Scope.READ, _NOW)
    elapsed = time.perf_counter() - start

    with pytest.raises(AssertionError):
        assert elapsed < absurdly_low_budget_sec
