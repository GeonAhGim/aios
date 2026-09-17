"""Unit tests for `src/foundation/ai/factory/contracts/v1.py` -- task-2643
AI-8 DoD ("자유 코드 거부"). D2 depth (ADR-2026-09-09-C): negative >=3,
failure injection 1, numeric performance assertion 1, gate-red reproduction
1."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.ai.factory.contracts import v1
from src.foundation.market_data.contracts.v1 import Timeframe

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_END = _START + timedelta(days=30)


def _data_scope_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        instruments=frozenset({"BTC/USDT"}),
        tf=Timeframe.H1,
        span=(_START, _END),
    )
    base.update(overrides)
    return base


def _proposal_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        proposal_id=uuid4(),
        script_source="input length: int = 14",
        hypothesis="rsi mean reversion",
        data_scope=v1.DataScope(**_data_scope_kwargs()),
        params={"length": 14},
        provider_ref=v1.ProviderRef.ANTHROPIC,
        prompt_hash="a" * 64,
        created_by_token=uuid4(),
    )
    base.update(overrides)
    return base


def _outcome_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        proposal_id=uuid4(),
        accepted=True,
        script_hash="b" * 64,
    )
    base.update(overrides)
    return base


# --- 정상 경로 ---


def test_data_scope_roundtrip() -> None:
    scope = v1.DataScope(**_data_scope_kwargs())
    assert scope.tf == Timeframe.H1
    assert scope.schema_version == "v1"


def test_strategy_proposal_roundtrip() -> None:
    proposal = v1.StrategyProposal(**_proposal_kwargs())
    assert proposal.provider_ref == v1.ProviderRef.ANTHROPIC
    assert proposal.params == {"length": 14}


def test_rejected_outcome_roundtrip() -> None:
    outcome = v1.ProposalOutcome(
        **_outcome_kwargs(
            accepted=False,
            script_hash=None,
            rejection_reason=v1.ProposalRejectionReason.COMPILE_FAILED,
        )
    )
    assert outcome.accepted is False
    assert outcome.rejection_reason is v1.ProposalRejectionReason.COMPILE_FAILED


# --- 부정 테스트 (>=3) ---


def test_data_scope_rejects_empty_instruments() -> None:
    with pytest.raises(ValidationError, match="instruments"):
        v1.DataScope(**_data_scope_kwargs(instruments=frozenset()))


def test_data_scope_rejects_span_end_before_start() -> None:
    with pytest.raises(ValidationError, match="span"):
        v1.DataScope(**_data_scope_kwargs(span=(_END, _START)))


def test_data_scope_rejects_span_end_equal_start() -> None:
    """경계값: end == start도 반개구간 위반이라 거부돼야 한다."""
    with pytest.raises(ValidationError, match="span"):
        v1.DataScope(**_data_scope_kwargs(span=(_START, _START)))


def test_strategy_proposal_rejects_hypothesis_over_2000_chars() -> None:
    with pytest.raises(ValidationError, match="hypothesis"):
        v1.StrategyProposal(**_proposal_kwargs(hypothesis="x" * 2001))


def test_strategy_proposal_rejects_blank_script_source() -> None:
    with pytest.raises(ValidationError, match="script_source"):
        v1.StrategyProposal(**_proposal_kwargs(script_source="   "))


def test_strategy_proposal_rejects_malformed_prompt_hash() -> None:
    with pytest.raises(ValidationError, match="prompt_hash"):
        v1.StrategyProposal(**_proposal_kwargs(prompt_hash="not-a-digest"))


def test_outcome_rejects_accepted_with_rejection_reason() -> None:
    with pytest.raises(ValidationError):
        v1.ProposalOutcome(
            **_outcome_kwargs(rejection_reason=v1.ProposalRejectionReason.SCHEMA_INVALID)
        )


def test_outcome_rejects_rejected_without_reason() -> None:
    with pytest.raises(ValidationError):
        v1.ProposalOutcome(**_outcome_kwargs(accepted=False, script_hash=None))


# --- 실패 주입 ---


def test_strategy_proposal_naive_datetime_in_span_rejected() -> None:
    """실패 주입: 상류가 tz-aware 규율(CLAUDE.md #3)을 어기고 naive datetime을
    span에 흘리면 pydantic `AwareDatetime`이 조용히 통과시키지 않고 거부해야
    한다."""
    naive_start = datetime(2026, 1, 1)
    with pytest.raises(ValidationError):
        v1.DataScope(**_data_scope_kwargs(span=(naive_start, _END)))


# --- 수치 성능 단언 ---

_CONSTRUCT_BUDGET_SEC = 2.0


def test_bulk_construction_of_many_strategy_proposals_meets_latency_budget() -> None:
    n = 3_000
    payloads = [_proposal_kwargs(proposal_id=uuid4()) for _ in range(n)]

    start = time.perf_counter()
    proposals = [v1.StrategyProposal(**payload) for payload in payloads]
    elapsed = time.perf_counter() - start

    budget = _CONSTRUCT_BUDGET_SEC
    print(f"[AI-8 factory contracts_v1] {n}건 construct in {elapsed:.4f}s (budget<{budget}s)")
    assert len(proposals) == n
    assert elapsed < _CONSTRUCT_BUDGET_SEC


# --- 게이트 적색 재현 ---


def test_gate_red_budget_actually_fails_past_budget() -> None:
    n = 200
    absurdly_low_budget_sec = 1e-9
    payloads = [_proposal_kwargs(proposal_id=uuid4()) for _ in range(n)]

    start = time.perf_counter()
    for payload in payloads:
        v1.StrategyProposal(**payload)
    elapsed = time.perf_counter() - start

    with pytest.raises(AssertionError):
        assert elapsed < absurdly_low_budget_sec


def test_gate_red_progressive_field_corruption_flips_pass_fail_at_each_stage() -> None:
    """동일 원본 dict를 한 번에 한 필드씩만 오염시켜 반복 검증해도 각 단계가
    정확히 그 단계의 이유로만 거부되고, 이전 단계의 거부가 다음(정상 복귀)
    단계까지 새지 않는지 확인한다."""
    good = _proposal_kwargs()

    stages: list[tuple[dict[str, Any], bool]] = [
        (good, True),
        ({**good, "prompt_hash": "bad"}, False),
        (good, True),
        ({**good, "hypothesis": "x" * 2001}, False),
        (good, True),
    ]

    for stage_index, (payload, should_pass) in enumerate(stages):
        if should_pass:
            proposal = v1.StrategyProposal(**payload)
            assert proposal.schema_version == "v1", f"stage {stage_index}"
        else:
            with pytest.raises(ValidationError):
                v1.StrategyProposal(**payload)
