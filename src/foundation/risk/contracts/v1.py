"""U-15 PERSONAL 모드 계약 v1 — YAML 설정 스키마 + API 응답 DTO.

Spec: task-2749. `PersonalRiskBundleConfigV1`은
`config/risk_policy/personal-conservative.yaml`의 스키마이고, 나머지는
`src/api/routers/personal.py`가 반환하는 응답 뷰다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "v1"


class _StrictModel(BaseModel):
    """미지 키를 조용히 무시하지 않는다(fail-closed) — risk_policy_loader.py와
    동일 원칙(오타·잘못된 위치의 키가 그대로 로드 성공으로 이어지는 것을
    막는다)."""

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
