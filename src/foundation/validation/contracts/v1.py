"""Strategy Validation 계약 v1.

Spec: AIOSproject 46_strategy_package_and_validation_specification_v1.0.md,
76_strategy_package_validation_l3_build_and_operational_specification_v1.0.md §4,
107_contract_versioning_and_compatibility_standard_v1.0.md.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class RunState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Outcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PASS_WITH_OBLIGATIONS = "PASS_WITH_OBLIGATIONS"


class StartValidationCommand(BaseModel):
    """76번 §4 `StartValidation`. `bars`는 76번 §1 "input snapshot ref"의
    실제 내용 — 호출자(라우터)가 어디서 가져오든(지금은 CredentialResolver
    +거래소 adapter의 get_ohlcv) 이 계약은 신경 쓰지 않는다."""

    strategy_id: str
    strategy_version: str
    cost_model_fee_bps: Decimal
    cost_model_slippage_bps: Decimal
    warmup_bars: int
    periods_per_year: int
    initial_equity: Decimal


class ValidationResultView(BaseModel):
    run_id: UUID
    strategy_id: str
    strategy_version: str
    check_type: str
    state: RunState
    outcome: Outcome | None
    metrics: dict[str, Any] | None
    warnings: list[str]
    hard_fail_reasons: list[str]
    obligations: list[str]
    result_hash: str | None
    created_at: datetime
    schema_version: str = SCHEMA_VERSION
