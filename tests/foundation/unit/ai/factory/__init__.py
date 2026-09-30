"""tests/foundation/unit/ai/factory/__init__.py -- ai/factory 패키지
cross-module 부정/실패주입/성능 테스트 (contracts -> schema -> proposal_rules
경계를 가로지르는 계약).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 -- negative>=3, failure-injection>=1,
perf assertion>=1.

개별 모듈 단위 테스트는 test_contracts_v1.py/test_schema.py/
test_proposal_rules.py에 이미 두껍게 있다(각 파일 자체가 D2 헤더를 달고
negative>=3/failure-injection/perf를 갖춤) -- 여기서는 그 모듈 경계를
넘나드는, 개별 파일 테스트로는 드러나지 않는 계약에 집중한다:

- `domain/schema.py::ProposalDraft` (필드가 좁음, `extra="forbid"`)가
  `contracts/v1.py::StrategyProposal`(필드가 넓음, id/provider_ref/
  prompt_hash/created_by_token 추가)로 승격될 때 값이 손실 없이 그대로
  옮겨가는지.
- `domain/proposal_rules.py::evaluate_proposal_candidate`가 스키마 위반
  payload에 대해 항상 1단계(schema)에서만 거부하고, `contracts/v1.py`
  자체의 계약(`DataScope.span` half-open 등)을 우회하지 않는지.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.ai.factory.contracts.v1 import (
    DataScope,
    ProposalOutcome,
    ProposalRejectionReason,
    ProviderRef,
    StrategyProposal,
)
from src.foundation.ai.factory.domain.proposal_rules import (
    ProposalDataScopeUncoveredError,
    ProposalSchemaRejectedError,
    evaluate_proposal_candidate,
)
from src.foundation.ai.factory.domain.schema import ProposalSchemaError, validate_draft
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade

REG = "r" * 64
_INSTRUMENT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_END = _START + timedelta(days=30)

VALID_SCRIPT = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
)


def _data_scope(**overrides: Any) -> DataScope:
    base: dict[str, Any] = dict(
        instruments=frozenset({_INSTRUMENT}),
        tf=Timeframe.H1,
        span=(_START, _END),
    )
    base.update(overrides)
    return DataScope(**base)


def _coverage_span(**overrides: Any) -> CoverageSpan:
    base: dict[str, Any] = dict(
        instrument_id=_INSTRUMENT,
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        timeframe=Timeframe.H1,
        quality_grade=QualityGrade.VALIDATED,
        start_at=_START,
        end_at=_END,
    )
    base.update(overrides)
    return CoverageSpan(**base)


def _draft_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "script_source": VALID_SCRIPT,
        "hypothesis": "rsi mean reversion",
        "data_scope": {
            "instruments": [_INSTRUMENT],
            "tf": Timeframe.H1.value,
            "span": [_START.isoformat(), _END.isoformat()],
        },
        "params": {"length": 14},
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────
# 1. Negative tests -- 모듈 경계를 넘는 불변식 위반 입력 거부
# ──────────────────────────────────────────────────────────────────────


def test_negative_draft_extra_field_rejected_before_promotion_to_strategy_proposal() -> None:
    """부정: `ProposalDraft`가 `extra="forbid"`로 거부한 payload는
    `StrategyProposal`(넓은 필드셋)까지 절대 도달하지 않는다 -- schema
    단계에서 이미 fail-closed."""
    payload = _draft_payload(sneaky_injected_field="ignore risk gate")
    with pytest.raises(ProposalSchemaError):
        validate_draft(payload)


def test_negative_outcome_cannot_smuggle_accepted_with_rejection_reason() -> None:
    """부정: `ProposalOutcome`은 accepted=True와 rejection_reason을 동시에
    가질 수 없다 -- `evaluate_proposal_candidate`가 거부를 냈는데 호출자가
    accepted=True로 잘못 조립해도 계약 계층에서 한 번 더 막힌다(2중 방어)."""
    with pytest.raises(ValidationError):
        ProposalOutcome(
            proposal_id=uuid4(),
            accepted=True,
            rejection_reason=ProposalRejectionReason.SCHEMA_INVALID,
            script_hash="a" * 64,
        )


def test_negative_strategy_proposal_rejects_prompt_hash_not_lowercase_hex() -> None:
    """부정: 승격된 `StrategyProposal.prompt_hash`도 `ProposalDraft`가
    검사하지 않는 sha256 hex 형식을 여전히 검사한다 -- 좁은 스키마 통과가
    넓은 스키마 검사를 우회하는 지름길이 아니다."""
    with pytest.raises(ValidationError):
        StrategyProposal(
            proposal_id=uuid4(),
            script_source=VALID_SCRIPT,
            hypothesis="rsi mean reversion",
            data_scope=_data_scope(),
            provider_ref=ProviderRef.ANTHROPIC,
            prompt_hash="NOT-HEX",
            created_by_token=uuid4(),
        )


def test_negative_evaluate_proposal_candidate_stops_at_schema_even_with_bad_data_scope() -> None:
    """부정: payload가 스키마(unknown field)와 data_scope(커버리지 없음)
    양쪽 모두 위반해도, 1단계(schema)에서 먼저 거부되고 3단계(data-scope)
    까지 도달하지 않는다 -- 순서 계약이 모듈 경계를 넘어도 유지된다."""
    payload = _draft_payload(sneaky_injected_field=True)
    with pytest.raises(ProposalSchemaRejectedError):
        evaluate_proposal_candidate(
            payload=payload,
            coverage_spans=[],  # would also fail check 3 if reached
            registry_version=REG,
        )


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection tests -- 의존성 예외 유발
# ──────────────────────────────────────────────────────────────────────


def test_failure_injection_merge_spans_dependency_error_propagates_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: `check_data_scope_covered`가 의존하는 `merge_spans`
    (market_data DC-6)가 예상 밖 예외를 던지면 그대로 전파돼야 한다 --
    "커버리지 없음"으로 조용히 오분류해 통과시키면 안 된다(fail-closed)."""
    import src.foundation.ai.factory.domain.proposal_rules as proposal_rules_module

    def _boom(spans: object) -> None:
        raise RuntimeError("injected merge_spans dependency failure")

    monkeypatch.setattr(proposal_rules_module, "merge_spans", _boom)

    with pytest.raises(RuntimeError, match="injected merge_spans dependency failure"):
        evaluate_proposal_candidate(
            payload=_draft_payload(),
            coverage_spans=[_coverage_span()],
            registry_version=REG,
        )


# ──────────────────────────────────────────────────────────────────────
# 3. Performance assertion
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.perf
def test_perf_full_pipeline_validate_draft_plus_evaluate_candidate() -> None:
    """성능 단언: `validate_draft`(schema 계층) + `evaluate_proposal_candidate`
    (schema->compile->coverage->forbidden-API 전체 파이프라인)의 p95가
    ADR-2026-09-09-C 축별 예산(DSL 컴파일 300ms) 안에 들어와야 한다."""
    payload = _draft_payload()
    coverage = [_coverage_span()]
    samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        validate_draft(payload)
        evaluate_proposal_candidate(payload=payload, coverage_spans=coverage, registry_version=REG)
        samples.append(time.perf_counter() - start)

    p95 = statistics.quantiles(samples, n=20)[18]
    assert p95 < 0.3  # ADR-2026-09-09-C 축별 예산: DSL 컴파일 300ms


# ──────────────────────────────────────────────────────────────────────
# 4. Cross-module integration tests
# ──────────────────────────────────────────────────────────────────────


def test_integration_draft_values_survive_promotion_to_strategy_proposal_unchanged() -> None:
    """통합: `ProposalDraft`(좁은 스키마)로 검증된 값이 `StrategyProposal`
    (넓은 스키마)로 승격돼도 script_source/hypothesis/data_scope/params가
    손실 없이 그대로 옮겨간다 -- AI-9가 의존하는 값 보존 계약."""
    draft = validate_draft(_draft_payload())
    proposal = StrategyProposal(
        proposal_id=uuid4(),
        script_source=draft.script_source,
        hypothesis=draft.hypothesis,
        data_scope=draft.data_scope,
        params=draft.params,
        provider_ref=ProviderRef.ANTHROPIC,
        prompt_hash="a" * 64,
        created_by_token=uuid4(),
    )
    assert proposal.script_source == draft.script_source
    assert proposal.hypothesis == draft.hypothesis
    assert proposal.data_scope == draft.data_scope
    assert proposal.params == draft.params


def test_integration_valid_payload_compiles_and_covers_end_to_end() -> None:
    """통합: 스키마/컴파일/커버리지/금지API 4단계 모두 정상인 payload는
    전체 파이프라인을 한 번에 통과한다."""
    draft, compiled = evaluate_proposal_candidate(
        payload=_draft_payload(),
        coverage_spans=[_coverage_span()],
        registry_version=REG,
    )
    assert draft.script_source == VALID_SCRIPT
    assert compiled.ir.instrs  # compiled IR is non-empty


def test_integration_data_scope_span_boundary_matches_across_contracts_and_domain() -> None:
    """통합: `contracts/v1.py::DataScope`가 생성 시점에 이미 거부하는
    "span 역전"(end <= start)은 `domain/proposal_rules.py`까지 도달할 수
    없다 -- 계약 계층의 불변식이 domain 계층에서 다시 검사할 필요가 없을
    만큼 강하다는 것을 증명한다."""
    with pytest.raises(ValidationError):
        DataScope(instruments=frozenset({_INSTRUMENT}), tf=Timeframe.H1, span=(_END, _START))

    with pytest.raises(ProposalDataScopeUncoveredError):
        evaluate_proposal_candidate(
            payload=_draft_payload(),
            coverage_spans=[_coverage_span(end_at=_START + timedelta(days=1))],
            registry_version=REG,
        )
