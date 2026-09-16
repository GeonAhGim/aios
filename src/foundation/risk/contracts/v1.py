"""U-15 PERSONAL mode contracts v1 — YAML config schema + API response DTOs.

Spec: task-2749. `PersonalRiskBundleConfigV1` is the schema for
`config/risk_policy/personal-conservative.yaml`; the rest are response
views returned by `src/api/routers/personal.py`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "v1"


class _StrictModel(BaseModel):
    """Does not silently ignore unknown keys (fail-closed) — same principle
    as risk_policy_loader.py (a typo or misplaced key must not load as a
    success)."""

    model_config = ConfigDict(extra="forbid")


class PersonalRiskBundleConfigV1(_StrictModel):
    name: str
    version: int = Field(gt=0)
    position_pct_of_equity: float = Field(gt=0, le=1)
    daily_loss_kill_pct: float = Field(gt=0, le=1)
    max_exposure_pct: float = Field(gt=0, le=1)
    default_notional_cap_krw: float = Field(gt=0)
    symbol_whitelist: list[str] = Field(default_factory=list)
    exchange_notional_caps: dict[str, float] = Field(default_factory=dict)


class PersonalRiskBundleView(BaseModel):
    name: str
    position_pct_of_equity: float
    daily_loss_kill_pct: float
    max_exposure_pct: float
    default_notional_cap_krw: float
    symbol_whitelist: list[str]
    schema_version: str = SCHEMA_VERSION


class PromotionChecklistView(BaseModel):
    eligible: bool
    blockers: list[str]
    min_paper_days: int
    paper_days_elapsed: int
    paper_violation_count: int
    schema_version: str = SCHEMA_VERSION


class KillSwitchView(BaseModel):
    engaged: bool
    reason: str | None = None
    schema_version: str = SCHEMA_VERSION


class DailyReportView(BaseModel):
    report_date: str
    realized_pnl_krw: float
    fill_count: int
    violation_count: int
    schema_version: str = SCHEMA_VERSION
