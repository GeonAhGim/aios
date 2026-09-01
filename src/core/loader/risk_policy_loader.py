"""7.3 — risk_policy.yaml 로더 + 스키마 검증.

Spec: 07_logging_config_v1.3.md#§7.2

8.2-B 원칙 — Risk 수치는 코드에 하드코딩하지 않고 이 파일로 관리한다.
Loader.load_config()(5.1)로 읽은 dict를 여기서 Pydantic 모델로 검증한다.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from src.core.loader.config_loader import load_config

DEFAULT_RISK_POLICY_PATH = Path(__file__).resolve().parents[3] / "config" / "risk_policy.yaml"


class DailyLossPolicy(BaseModel):
    warning_pct: float
    halt_pct: float


class MaxDrawdownPolicy(BaseModel):
    warning_pct: float
    hard_stop_pct: float


class CoverageMultiplier(BaseModel):
    high: float
    medium: float
    low: float


class LeveragePolicy(BaseModel):
    default_max: float
    coverage_multiplier: CoverageMultiplier


class PositionConcentrationPolicy(BaseModel):
    single_asset_max_pct: float


class StrategyAllocationPolicy(BaseModel):
    unverified_max_pct: float
    certified_level4_max_pct: float


class VarPolicy(BaseModel):
    confidence: float
    horizon_days: int
    max_pct: float


class CorrelationRiskPolicy(BaseModel):
    threshold: float
    aggregate_exposure_max_pct: float


class TradeFrequencyPolicy(BaseModel):
    anomaly_multiplier: float


class CircuitBreakerWarning(BaseModel):
    api_error_rate_pct: float
    data_delay_sec: float


class CircuitBreakerRestricted(BaseModel):
    api_error_rate_pct: float
    order_reject_rate_pct: float


class CircuitBreakerHalted(BaseModel):
    data_delay_sec: float


class CircuitBreakerEmergency(BaseModel):
    daily_loss_pct: float
    api_disconnect_sec: float


class CircuitBreakerPolicy(BaseModel):
    warning: CircuitBreakerWarning
    restricted: CircuitBreakerRestricted
    halted: CircuitBreakerHalted
    emergency: CircuitBreakerEmergency


class WatchdogPolicy(BaseModel):
    loss_threshold_pct: float
    unresponsive_sec: int
    window_min: int


class DataDistrustPolicy(BaseModel):
    enter_threshold_pct: float
    exit_threshold_pct: float
    exit_sustain_sec: int


class ExecutionLoopPolicy(BaseModel):
    """FD-8.1 — 실행 루프 폴링 주기(리스크 수치는 아니지만 판단 계층
    설정의 단일 출처 원칙에 따라 이 파일에 함께 둔다)."""

    interval_sec: float


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
