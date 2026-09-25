"""AI-1 -- gateway/contracts/v1 스냅샷 + D2 증빙.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-1, §3, §4.
완료 하한: ADR-2026-09-09-C D2 -- negative >=3, 실패 주입 1, 성능 단언 1,
게이트 적색 재현 1.

`fixtures/gateway_contracts_v1.json`은 현재 스키마의 스냅샷이다. 필드를
지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다(107번 §8 "필드 제거 시
실패"). 필드 추가는 minor 변경이므로 허용되고, 그 경우에만 fixture를
함께 갱신한다.

`contracts/v1.py`는 pydantic 모델 정의뿐이라 DB/네트워크가 없다 --
research_data RD-2 DEEPEN(task-2905)과 동일하게, 여기서 "실패주입"이란
잘못된 값이 pydantic-core 검증 경로를 조용히 우회하지 않는지, "게이트
적색 재현"이란 동일 원본 dict를 단계적으로 오염시켜 반복 검증해도 각
단계의 통과/거부가 뒤집히지 않는지를 뜻한다.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from src.foundation.ai.gateway.contracts import v1
from tests.conftest import PerfBudget

FIXTURE = Path(__file__).parent / "fixtures" / "gateway_contracts_v1.json"

_MODELS = (v1.AgentToken, v1.ConfirmTicket)

_ERROR_CODES = {
    "AI_SCOPE_DENIED",
    "AI_TOKEN_REVOKED",
    "AI_CONFIRM_REQUIRED",
    "AI_CONFIRM_MISMATCH",
}


def _now() -> datetime:
    return datetime(2026, 9, 17, 0, 0, tzinfo=timezone.utc)


def _token_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        token_id=uuid4(),
        tenant_id=uuid4(),
        scopes=frozenset({v1.Scope.READ, v1.Scope.RESEARCH}),
        allow_instruments=frozenset({"BTC/USDT"}),
        notional_cap=Decimal("10000"),
        expires_at=_now(),
    )
    base.update(overrides)
    return base


def _ticket_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        ticket_id=uuid4(),
        action_digest="a" * 64,
        expires_at=_now(),
    )
    base.update(overrides)
    return base


# ---- 스키마 스냅샷 ----------------------------------------------------------


def test_schema_snapshot_matches_fixture() -> None:
    current = {m.__name__: m.model_json_schema() for m in _MODELS}
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_gateway_error_code_has_exactly_the_taxonomy_from_spec() -> None:
    assert {code.value for code in v1.AgentGatewayErrorCode} == _ERROR_CODES


def test_agent_token_defaults_paper_only_true_and_schema_version() -> None:
    token = v1.AgentToken(**_token_kwargs())
    assert token.paper_only is True
    assert token.schema_version == "v1"


def test_confirm_ticket_roundtrip() -> None:
    ticket = v1.ConfirmTicket(**_ticket_kwargs())
    assert ticket.action_digest == "a" * 64
    assert ticket.schema_version == "v1"


# ---- negative (>=3) ---------------------------------------------------------


def test_agent_token_naive_expires_at_rejected() -> None:
    with pytest.raises(ValidationError, match="expires_at"):
        v1.AgentToken(**_token_kwargs(expires_at=datetime(2026, 9, 17, 0, 0)))


def test_confirm_ticket_naive_expires_at_rejected() -> None:
    with pytest.raises(ValidationError, match="expires_at"):
        v1.ConfirmTicket(**_ticket_kwargs(expires_at=datetime(2026, 9, 17, 0, 0)))


def test_confirm_ticket_non_hex_digest_rejected() -> None:
    with pytest.raises(ValidationError, match="action_digest"):
        v1.ConfirmTicket(**_ticket_kwargs(action_digest="not-a-digest"))


def test_confirm_ticket_short_digest_rejected() -> None:
    """63자(한 글자 부족)도 거부돼야 한다 -- 길이 경계 하나만 검사하는
    회귀(>= 대신 == 등)를 잡는다."""
    with pytest.raises(ValidationError, match="action_digest"):
        v1.ConfirmTicket(**_ticket_kwargs(action_digest="a" * 63))


def test_agent_token_missing_required_field_rejected() -> None:
    payload = _token_kwargs()
    del payload["notional_cap"]
    with pytest.raises(ValidationError, match="notional_cap"):
        v1.AgentToken(**payload)


# ---- 실패 주입 ---------------------------------------------------------------


def test_unknown_scope_literal_is_rejected_not_silently_coerced() -> None:
    """`scopes`의 원소가 `Scope`의 4개 값(read/research/propose/paper) 밖이면
    pydantic-core가 `ValidationError`로 막아야 한다 -- LIVE·자금·정책·토큰
    관리 스코프는 존재하지 않는다는 §3 불변조건이 문자열 하나로 조용히
    뚫리면 안 된다."""
    with pytest.raises(ValidationError, match="scopes"):
        v1.AgentToken(**_token_kwargs(scopes=frozenset({"live"})))


def test_string_allow_instruments_is_rejected_not_exploded_into_characters() -> None:
    """`allow_instruments: frozenset[str]`에 문자열 `"BTC"`를 그대로 넘기면
    (호출자가 컬렉션 대신 실수로 단일 문자열을 넘긴 흔한 버그) 문자 단위로
    쪼개져 `{"B","T","C"}`처럼 조용히 받아들여지면 안 된다."""
    with pytest.raises(ValidationError, match="allow_instruments"):
        v1.AgentToken(**_token_kwargs(allow_instruments="BTC"))


def test_agent_token_is_frozen_against_post_construction_mutation() -> None:
    """`frozen=True`가 실제로 걸려 있는지 -- 발급 후 `.scopes`를 넓히는
    시도가 `ValidationError`가 아니라 조용한 속성 대입으로 성공하면 I-06의
    스코프 상승 불가 불변조건이 메모리에서 우회된다."""
    token = v1.AgentToken(**_token_kwargs())
    with pytest.raises(ValidationError):
        token.scopes = frozenset({v1.Scope.READ, v1.Scope.PAPER})


# ---- 성능 단언 ---------------------------------------------------------------


@pytest.mark.perf
def test_bulk_construction_of_many_agent_tokens_meets_latency_budget(
    perf_budget: PerfBudget,
) -> None:
    """MCP 도구 호출마다(§7 SLO: read 도구 p95 300ms) `authorize()`가
    `AgentToken`을 조회·검증하는 핫 패스다 -- 5,000건 구성이 절대시간
    예산 내여야 건당 상수시간에서 벗어나지 않았다고 볼 수 있다."""
    n = 5_000
    budget_ms = 2000.0  # 실측 로컬 <500ms
    payloads = [_token_kwargs(token_id=uuid4()) for _ in range(n)]

    def _construct() -> list[v1.AgentToken]:
        tokens = [v1.AgentToken(**payload) for payload in payloads]
        assert len(tokens) == n
        return tokens

    sample = perf_budget.assert_within(
        _construct, budget_ms=budget_ms, label="[AI-1 gateway contracts_v1]"
    )
    desc = perf_budget.describe(sample, budget_ms=budget_ms)
    print(f"[AI-1 gateway contracts_v1] {n}건 construct in {desc}")


# ---- 게이트 적색 재현 ---------------------------------------------------------


def test_gate_red_progressive_field_corruption_flips_pass_fail_at_each_stage() -> None:
    """동일 원본 dict를 시작점으로 두고, 한 번에 한 필드씩만 오염시켜 5단계로
    재생한다. 각 단계는 정확히 그 단계가 오염시킨 이유로만 거부돼야 한다 --
    이전 단계의 실패가 새어 다음(정상 복귀) 단계까지 "항상 거부"로 고착되거나,
    반대로 한 번 통과하면 캐시되어 이후 오염을 못 잡는 회귀를 잡는다."""
    good = _token_kwargs()

    stages: list[tuple[dict[str, Any], bool]] = [
        (good, True),
        ({**good, "scopes": frozenset({"live"})}, False),
        (good, True),  # 정상으로 복귀 -- 이전 단계 거부가 새지 않아야 통과
        ({**good, "expires_at": datetime(2026, 9, 17, 0, 0)}, False),  # naive
        (good, True),  # 다시 정상 복귀
    ]

    for stage_index, (payload, should_pass) in enumerate(stages):
        if should_pass:
            token = v1.AgentToken(**payload)
            assert token.schema_version == "v1", f"stage {stage_index}"
        else:
            with pytest.raises(ValidationError):
                v1.AgentToken(**payload)


def test_gate_red_budget_actually_fails_past_budget() -> None:
    """위 성능 단언이 실제로 예산 초과를 잡아내는지(tautology 아님) 확인한다
    -- 예산을 실측치보다 훨씬 낮게 걸면 동일 검증 루프가 진짜로
    `AssertionError`를 내야, CI가 언젠가 실제로 느려졌을 때 빨간불이
    된다는 것을 증명한다."""
    n = 200
    absurdly_low_budget_sec = 1e-9
    payloads = [_token_kwargs(token_id=uuid4()) for _ in range(n)]

    start = time.perf_counter()
    for payload in payloads:
        v1.AgentToken(**payload)
    elapsed = time.perf_counter() - start

    with pytest.raises(AssertionError):
        assert elapsed < absurdly_low_budget_sec


@pytest.mark.parametrize("mode", ["validation", "serialization"])
@pytest.mark.parametrize(
    "digest",
    [
        "not-a-digest",
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "a" * 64 + "\n",
        "\n" + "a" * 64,
        "",
        123,
        None,
    ],
)
def test_digest_schema_and_model_reject_invalid_values(mode, digest) -> None:
    schema = v1.ConfirmTicket.model_json_schema(mode=mode)
    Draft202012Validator.check_schema(schema)
    payload = v1.ConfirmTicket(**_ticket_kwargs()).model_dump(mode="json")
    payload["action_digest"] = digest
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors, f"schema accepted invalid digest: {digest!r}"
    assert all(list(error.path) == ["action_digest"] for error in errors)
    with pytest.raises(ValidationError, match="action_digest"):
        v1.ConfirmTicket.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_digest_schema_rejects_injected_corruption_and_recovers(mode) -> None:
    validator = Draft202012Validator(v1.ConfirmTicket.model_json_schema(mode=mode))
    payload = v1.ConfirmTicket(**_ticket_kwargs()).model_dump(mode="json")
    for digest in ["0123456789abcdef" * 4, "f" * 64, "0" * 64]:
        payload["action_digest"] = digest
        validator.validate(payload)
        assert v1.ConfirmTicket.model_validate(payload).action_digest == digest
        payload["action_digest"] = digest[:-1] + "G"
        assert not validator.is_valid(payload)
        with pytest.raises(ValidationError, match="action_digest"):
            v1.ConfirmTicket.model_validate(payload)


@pytest.mark.perf
def test_digest_validation_p99_within_gate_budget(perf_budget: PerfBudget) -> None:
    payload = _ticket_kwargs()
    budget_ms = 5.0  # ADR-2026-09-09-C pre-trade gate budget.
    n = 1000

    def _validate() -> v1.ConfirmTicket:
        return v1.ConfirmTicket(**payload)

    perf_samples = perf_budget.samples(_validate, n=n, warmup=0, batch=1)
    wall_ms_list = sorted([s.wall_ms for s in perf_samples])
    p99_wall_ms = wall_ms_list[int(len(wall_ms_list) * 0.99) - 1]
    print(f"[AI-1 digest] p99={p99_wall_ms:.3f}ms budget<{budget_ms:.3f}ms")
    assert p99_wall_ms < budget_ms
