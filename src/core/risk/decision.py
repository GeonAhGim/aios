"""L4_risk_and_safety_v1.0.md#3.1, #9 R-02 — `RiskDecision`(48번 §2 1:1).

이 모듈은 순수 값 객체만 정의한다. 결정을 실제로 만드는 것은
`evaluator.py`(R-16)이고, 영속화는 `risk_decision_recorder.py`(R-25)다.
5단계 outcome(ALLOW/DENY/REDUCE/PAUSE/ESCALATE)·rule_hash·inputs_hash·
trace_id·TTL을 하나의 계약으로 묶어 재생(replay)과 감사를 가능하게 하는
것이 R2(결정론·재생) 요구의 핵심이다.

호환 규칙(107번): 필드 추가는 MINOR(기본값 필수). `RiskOutcome`/`GateKind`
값 추가는 MINOR, 제거·의미 변경은 MAJOR(`v2`, `schema_version` 갱신).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator

SCHEMA_VERSION: Literal["v1"] = "v1"


class RiskOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REDUCE = "REDUCE"
    PAUSE = "PAUSE"
    ESCALATE = "ESCALATE"


class GateKind(str, Enum):
    DEPLOYMENT = "DEPLOYMENT"
    PRE_INTENT = "PRE_INTENT"
    PRE_TRADE = "PRE_TRADE"
    PRE_SUBMIT = "PRE_SUBMIT"
    INTRADAY = "INTRADAY"
    RECOVERY = "RECOVERY"


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않는다 — tz-aware UTC만 사용한다")
    return value


class RuleResult(BaseModel, frozen=True):
    rule_id: str
    outcome: RiskOutcome
    reason_code: str | None = None
    observed: Decimal | None = None
    limit: Decimal | None = None
    unit: Literal["pct", "x", "count", "notional"]
    missing_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _missing_fields_imply_deny(self) -> RuleResult:
        # I2 — 판단 불가를 승인으로 취급하지 않는다(fail-closed).
        if self.missing_fields and self.outcome != RiskOutcome.DENY:
            raise ValueError("missing_fields가 비어있지 않으면 outcome은 DENY여야 한다")
        return self


class RiskDecision(BaseModel, frozen=True):
    schema_version: Literal["v1"] = SCHEMA_VERSION
    decision_id: UUID
    gate_kind: GateKind
    tenant_id: UUID
    execution_ref: str | None
    subject_fingerprint: str
    outcome: RiskOutcome
    reason_codes: tuple[str, ...]
    obligations: tuple[str, ...]
    rule_results: tuple[RuleResult, ...]
    rule_version: str
    rule_hash: str
    engine_version: str
    inputs_hash: str
    input_refs: tuple[str, ...]
    evaluated_at: datetime
    expires_at: datetime
    trace_id: UUID
    evidence_ref: str | None
    latency_us: int

    @field_validator("evaluated_at", "expires_at")
    @classmethod
    def _validate_aware_utc(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    def is_actionable(self, now: datetime) -> bool:
        """만료 전이고 outcome이 실행 가능한 종류(ALLOW/REDUCE)일 때만 True."""
        _require_aware_utc(now)
        return self.outcome in (RiskOutcome.ALLOW, RiskOutcome.REDUCE) and now < self.expires_at
