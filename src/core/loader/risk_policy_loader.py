"""7.3 — risk_policy.yaml 로더 + 스키마 검증.

Spec: 07_logging_config_v1.3.md#§7.2

8.2-B 원칙 — Risk 수치는 코드에 하드코딩하지 않고 이 파일로 관리한다.
Loader.load_config()(5.1)로 읽은 dict를 여기서 Pydantic 모델로 검증한다.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from src.core.loader.config_loader import load_config

DEFAULT_RISK_POLICY_PATH = Path(__file__).resolve().parents[3] / "config" / "risk_policy.yaml"

# 레드팀 감사(docs/RED_TEAM_FINDINGS.md #14) 반영 — 아래 모델들은 전부
# 퍼센트/배수/기간 필드에 범위 제약을 건다. 이 파일(config/risk_policy.yaml)은
# CODEOWNERS상 FROZEN Zone과 동일하게 취급되는 유일한 방어선이라, 오타
# 하나(음수 임계값, 100% 넘는 상한, 0으로 비워진 서킷브레이커 값 등)가
# 그대로 운영 정책이 되는 것을 막는다.
_PCT = Field(gt=0, le=100)


class DailyLossPolicy(BaseModel):
    warning_pct: float = _PCT
    halt_pct: float = _PCT


class MaxDrawdownPolicy(BaseModel):
    warning_pct: float = _PCT
    hard_stop_pct: float = _PCT


class CoverageMultiplier(BaseModel):
    high: float = Field(gt=0)
    medium: float = Field(gt=0)
    low: float = Field(gt=0)


class LeveragePolicy(BaseModel):
    default_max: float = Field(gt=0)
    coverage_multiplier: CoverageMultiplier


class PositionConcentrationPolicy(BaseModel):
    single_asset_max_pct: float = _PCT


class StrategyAllocationPolicy(BaseModel):
    unverified_max_pct: float = _PCT
    certified_level4_max_pct: float = _PCT


class VarPolicy(BaseModel):
    confidence: float = Field(gt=0, lt=1)
    horizon_days: int = Field(gt=0)
    max_pct: float = _PCT
    # §3.3/R-11 rules/var_es.py가 소비 — ES 별도 상한과 최소 표본 수(미달=결손).
    es_max_pct: float = _PCT
    min_bars: int = Field(gt=0)


class CorrelationRiskPolicy(BaseModel):
    threshold: float = Field(gt=0, le=1)  # 상관계수 크기 — 이론적 상한은 1
    aggregate_exposure_max_pct: float = _PCT


class TradeFrequencyPolicy(BaseModel):
    anomaly_multiplier: float = Field(gt=0)
    # §3.3/R-12 rules/trade_frequency.py가 소비 — 배수 상한과 절대 상한 중 큰 값.
    max_trades_per_hour: int = Field(gt=0)


class CircuitBreakerWarning(BaseModel):
    api_error_rate_pct: float = _PCT
    data_delay_sec: float = Field(gt=0)


class CircuitBreakerRestricted(BaseModel):
    api_error_rate_pct: float = _PCT
    order_reject_rate_pct: float = _PCT


class CircuitBreakerHalted(BaseModel):
    data_delay_sec: float = Field(gt=0)


class CircuitBreakerEmergency(BaseModel):
    daily_loss_pct: float = _PCT
    api_disconnect_sec: float = Field(gt=0)


class CircuitBreakerPolicy(BaseModel):
    warning: CircuitBreakerWarning
    restricted: CircuitBreakerRestricted
    halted: CircuitBreakerHalted
    emergency: CircuitBreakerEmergency


class WatchdogPolicy(BaseModel):
    loss_threshold_pct: float = _PCT
    unresponsive_sec: int = Field(gt=0)
    window_min: int = Field(gt=0)


class DataDistrustPolicy(BaseModel):
    enter_threshold_pct: float = _PCT
    exit_threshold_pct: float = _PCT
    exit_sustain_sec: int = Field(gt=0)


class ExecutionLoopPolicy(BaseModel):
    """FD-8.1 — 실행 루프 폴링 주기(리스크 수치는 아니지만 판단 계층
    설정의 단일 출처 원칙에 따라 이 파일에 함께 둔다)."""

    interval_sec: float = Field(gt=0)


class RiskPolicy(BaseModel):
    """risk_policy.yaml 전체 구조(§7.2) 1:1 대응."""

    version: str
    daily_loss: DailyLossPolicy
    max_drawdown: MaxDrawdownPolicy
    leverage: LeveragePolicy
    position_concentration: PositionConcentrationPolicy
    strategy_allocation: StrategyAllocationPolicy
    var: VarPolicy
    correlation_risk: CorrelationRiskPolicy
    trade_frequency: TradeFrequencyPolicy
    circuit_breaker: CircuitBreakerPolicy
    watchdog: WatchdogPolicy
    data_distrust: DataDistrustPolicy
    execution_loop: ExecutionLoopPolicy


def load_risk_policy(path: Path = DEFAULT_RISK_POLICY_PATH) -> RiskPolicy:
    """스키마 위반 시 pydantic.ValidationError로 실패한다 — 조용히 기본값으로
    대체하지 않는다(8.2-B 수치는 항상 명시적이어야 함)."""
    return RiskPolicy(**load_config(path))
