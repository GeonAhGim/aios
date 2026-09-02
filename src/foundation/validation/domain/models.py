"""Strategy Validation 도메인 모델 — pure value object.

Spec: AIOSproject 76_strategy_package_validation_l3_build_and_operational_
specification_v1.0.md §1/§3.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class RunState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Outcome(str, Enum):
    """76번 §3 "PASS, FAIL, PASS_WITH_OBLIGATIONS만 반환한다."""

    PASS = "PASS"
    FAIL = "FAIL"
    PASS_WITH_OBLIGATIONS = "PASS_WITH_OBLIGATIONS"


@dataclass(frozen=True)
class ValidationRun:
    id: UUID
    strategy_id: str
    strategy_version: str
    check_type: str
    input_snapshot_hash: str
    cost_model: dict[str, Any]
    warmup_bars: int
    periods_per_year: int
    initial_equity: Decimal
    state: RunState
    created_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class ValidationResult:
    id: UUID
    run_id: UUID
    outcome: Outcome
    metrics: dict[str, Any]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    hard_fail_reasons: tuple[str, ...] = field(default_factory=tuple)
    obligations: tuple[str, ...] = field(default_factory=tuple)
    result_hash: str = ""
    created_at: datetime | None = None
