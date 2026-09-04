"""7.3 — risk_policy.yaml 로더 + 스키마 검증.

Spec: 07_logging_config_v1.3.md#§7.2

8.2-B 원칙 — Risk 수치는 코드에 하드코딩하지 않고 이 파일로 관리한다.
Loader.load_config()(5.1)로 읽은 dict를 여기서 Pydantic 모델로 검증한다.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.loader.config_loader import load_config

if TYPE_CHECKING:
    from src.core.risk.policy_bundle import RiskRuleBundle

DEFAULT_RISK_POLICY_PATH = Path(__file__).resolve().parents[3] / "config" / "risk_policy.yaml"

# 레드팀 감사(docs/RED_TEAM_FINDINGS.md #14) 반영 — 아래 모델들은 전부
# 퍼센트/배수/기간 필드에 범위 제약을 건다. 이 파일(config/risk_policy.yaml)은
# CODEOWNERS상 FROZEN Zone과 동일하게 취급되는 유일한 방어선이라, 오타
# 하나(음수 임계값, 100% 넘는 상한, 0으로 비워진 서킷브레이커 값 등)가
# 그대로 운영 정책이 되는 것을 막는다.
_PCT = Field(gt=0, le=100)


class _StrictModel(BaseModel):
    """미지 키를 조용히 무시하지 않는다 — 오타·잘못된 위치의 키가 그대로
    로드 성공으로 이어지는 것을 막는다(fail-closed, DoD 미지 키 거부)."""

    model_config = ConfigDict(extra="forbid")


class DailyLossPolicy(_StrictModel):
    warning_pct: float = _PCT
    halt_pct: float = _PCT


class MaxDrawdownPolicy(_StrictModel):
    warning_pct: float = _PCT
    hard_stop_pct: float = _PCT


class CoverageMultiplier(_StrictModel):
    high: float = Field(gt=0)
    medium: float = Field(gt=0)
    low: float = Field(gt=0)


class LeveragePolicy(_StrictModel):
    default_max: float = Field(gt=0)
    coverage_multiplier: CoverageMultiplier


class PositionConcentrationPolicy(_StrictModel):
    single_asset_max_pct: float = _PCT


class StrategyAllocationPolicy(_StrictModel):
    unverified_max_pct: float = _PCT
    certified_level4_max_pct: float = _PCT


class VarPolicy(_StrictModel):
    confidence: float = Field(gt=0, lt=1)
    horizon_days: int = Field(gt=0)
    max_pct: float = _PCT
    # §3.3/R-11 rules/var_es.py가 소비 — ES 별도 상한과 최소 표본 수(미달=결손).
    es_max_pct: float = _PCT
    min_bars: int = Field(gt=0)
    # task-1194 R-21 §3.3 확장 — VaR 추정 방법·타임프레임·룩백 창.
    method: str
    timeframe: str
    lookback_bars: int = Field(gt=0)

    @model_validator(mode="after")
    def _min_bars_within_lookback(self) -> VarPolicy:
        if self.min_bars > self.lookback_bars:
            raise ValueError("min_bars는 lookback_bars를 넘을 수 없다(최소 표본 > 룩백 창)")
        return self


class CorrelationRiskPolicy(_StrictModel):
    threshold: float = Field(gt=0, le=1)  # 상관계수 크기 — 이론적 상한은 1
    aggregate_exposure_max_pct: float = _PCT
    # task-1194 R-21 §3.3 확장 — 상관계수 룩백 창·최소 중첩 표본·EWMA 감쇠계수.
    lookback_bars: int = Field(gt=0)
    min_overlap: int = Field(gt=0)
    ewma_lambda: float | None = None

    @model_validator(mode="after")
    def _min_overlap_within_lookback(self) -> CorrelationRiskPolicy:
        if self.min_overlap > self.lookback_bars:
            raise ValueError("min_overlap은 lookback_bars를 넘을 수 없다")
        if self.ewma_lambda is not None and not (0 < self.ewma_lambda < 1):
            raise ValueError("ewma_lambda는 (0, 1) 구간이거나 null이어야 한다")
        return self


class TradeFrequencyPolicy(_StrictModel):
    anomaly_multiplier: float = Field(gt=0)
    # §3.3/R-12 rules/trade_frequency.py가 소비 — 배수 상한과 절대 상한 중 큰 값.
    max_trades_per_hour: int = Field(gt=0)


class DecisionTtlPolicy(_StrictModel):
    """task-1194 R-21 §3.3 확장 — PRE_TRADE/PRE_SUBMIT/배포 결정의 TTL."""

    pre_trade_sec: float = Field(gt=0)
    pre_submit_sec: float = Field(gt=0)
    deployment_sec: float = Field(gt=0)


class ReactivationPolicy(_StrictModel):
    """task-1194 R-21 §3.3 확장 — halted/emergency 하향(재가동) 절차."""

    cooldown_sec: int = Field(gt=0)
    approval_ttl_sec: int = Field(gt=0)
    evidence_required: bool


class LiquidationPolicy(_StrictModel):
    """task-1194 R-21 §3.3 확장 — 8.6-A-1 강제청산 분할·지터·참여율 상한."""

    max_participation_pct: float = _PCT
    slice_count_min: int = Field(gt=0)
    slice_count_max: int = Field(gt=0)
    size_jitter_pct: float = _PCT
    interval_min_sec: float = Field(gt=0)
    interval_max_sec: float = Field(gt=0)
    max_slice_notional: float = Field(gt=0)
    limit_tolerance_bps: float = Field(gt=0)
    slice_ttl_sec: float = Field(gt=0)
    adverse_move_abort_pct: float = _PCT
    total_deadline_sec: float = Field(gt=0)

    @model_validator(mode="after")
    def _slice_and_interval_bounds_ordered(self) -> LiquidationPolicy:
        if self.slice_count_min > self.slice_count_max:
            raise ValueError("slice_count_min은 slice_count_max를 넘을 수 없다")
        if self.interval_min_sec > self.interval_max_sec:
            raise ValueError("interval_min_sec은 interval_max_sec을 넘을 수 없다")
        return self


class CircuitBreakerWarning(_StrictModel):
    api_error_rate_pct: float = _PCT
    data_delay_sec: float = Field(gt=0)


class CircuitBreakerRestricted(_StrictModel):
    api_error_rate_pct: float = _PCT
    order_reject_rate_pct: float = _PCT


class CircuitBreakerHalted(_StrictModel):
    data_delay_sec: float = Field(gt=0)


class CircuitBreakerEmergency(_StrictModel):
    daily_loss_pct: float = _PCT
    api_disconnect_sec: float = Field(gt=0)


class CircuitBreakerPolicy(_StrictModel):
    warning: CircuitBreakerWarning
    restricted: CircuitBreakerRestricted
    halted: CircuitBreakerHalted
    emergency: CircuitBreakerEmergency


class WatchdogPolicy(_StrictModel):
    loss_threshold_pct: float = _PCT
    unresponsive_sec: int = Field(gt=0)
    window_min: int = Field(gt=0)


class DataDistrustPolicy(_StrictModel):
    enter_threshold_pct: float = _PCT
    exit_threshold_pct: float = _PCT
    exit_sustain_sec: int = Field(gt=0)
    # task-1194 R-21 §3.3 확장 — 쿼럼 최소 소스 수·시세 타임아웃.
    min_sources: int = Field(gt=0)
    quote_timeout_sec: float = Field(gt=0)

    @model_validator(mode="after")
    def _exit_below_enter(self) -> DataDistrustPolicy:
        # 히스테리시스 — exit 임계값이 enter 임계값보다 낮아야 진동 없이
        # DISTRUSTED에서 정상으로 복귀할 수 있다.
        if self.exit_threshold_pct >= self.enter_threshold_pct:
            raise ValueError("exit_threshold_pct는 enter_threshold_pct보다 작아야 한다")
        return self


class ExecutionLoopPolicy(_StrictModel):
    """FD-8.1 — 실행 루프 폴링 주기(리스크 수치는 아니지만 판단 계층
    설정의 단일 출처 원칙에 따라 이 파일에 함께 둔다)."""

    interval_sec: float = Field(gt=0)


class RiskPolicy(_StrictModel):
    """risk_policy.yaml 전체 구조(§7.2/§3.3) 1:1 대응."""

    version: str
    daily_loss: DailyLossPolicy
    max_drawdown: MaxDrawdownPolicy
    leverage: LeveragePolicy
    position_concentration: PositionConcentrationPolicy
    strategy_allocation: StrategyAllocationPolicy
    var: VarPolicy
    correlation_risk: CorrelationRiskPolicy
    trade_frequency: TradeFrequencyPolicy
    decision_ttl: DecisionTtlPolicy
    reactivation: ReactivationPolicy
    liquidation: LiquidationPolicy
    circuit_breaker: CircuitBreakerPolicy
    watchdog: WatchdogPolicy
    data_distrust: DataDistrustPolicy
    execution_loop: ExecutionLoopPolicy


def load_risk_policy(path: Path = DEFAULT_RISK_POLICY_PATH) -> RiskPolicy:
    """스키마 위반 시 pydantic.ValidationError로 실패한다 — 조용히 기본값으로
    대체하지 않는다(8.2-B 수치는 항상 명시적이어야 함)."""
    return RiskPolicy(**load_config(path))


class BundleMismatchError(Exception):
    """§4.1 I6 — yaml에서 로드한 정책의 rule_hash가 scope의 ACTIVE
    `RiskRuleBundle.rule_hash`와 다르면 모든 결정을 DENY해야 한다."""


def verify_policy_against_bundle(policy: RiskPolicy, bundle: RiskRuleBundle) -> None:
    """§4.1 I6 강제 — `policy`로부터 재계산한 rule_hash가 `bundle.rule_hash`와
    일치하지 않으면 `BundleMismatchError`를 던진다. 해시 재계산은 R-15
    `policy_bundle.compute_rule_hash`를 그대로 재사용한다(새 해시 함수를
    만들지 않는다). 순수 함수 — 번들 레코드는 인자로만 받는다(조회는
    R-22 저장소의 책임).
    """
    from src.core.risk.policy_bundle import compute_rule_hash

    recomputed = compute_rule_hash(policy, bundle.engine_version)
    if recomputed != bundle.rule_hash:
        raise BundleMismatchError(
            f"policy rule_hash({recomputed}) != ACTIVE bundle rule_hash({bundle.rule_hash})"
        )
