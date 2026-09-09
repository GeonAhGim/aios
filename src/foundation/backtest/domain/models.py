"""Backtest Simulation Engine 도메인 모델 — DB/HTTP 없이 순수 데이터.

Spec: AIOSproject 109_backtest_simulation_engine_l3_build_and_operational_
specification_v1.0.md §3, §5; docs/specs/L4_strategy_portfolio_backtest_v1.0.md
§2.4 (`domain/models.py` row -- MINOR extension of `CostModel`/`BacktestConfig`/
`BacktestMetrics`, existing fields unchanged).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.core.risk.hashing import canonical_json, sha256_hex
from src.data.models.trading import OrderSide


class CostModel(BaseModel):
    """46번 §2 "Backtest" 행 — 비용모델 없는 백테스트는 거부 대상이라
    기본값을 두지 않는다(호출자가 반드시 명시적으로 선택하게 강제).

    v1은 선형 모델(고정 bps)만 지원한다 — 호가창 깊이/시장충격 기반
    비선형 슬리피지는 후속 revision 대상(46번 §2 Capacity 행).

    v2 (MINOR, §2.4): adds maker/taker split, spread, a non-linear slippage
    choice (`SQRT_IMPACT`), a market-impact coefficient and funding cost.
    The existing `fee_bps` remains the taker-fee alias for backward
    compatibility -- callers that do not set `maker_fee_bps`/`taker_fee_bps`
    inherit `fee_bps` for both (an explicit inheritance, not a silent
    zero-fill)."""

    fee_bps: Decimal
    slippage_bps: Decimal
    maker_fee_bps: Decimal | None = None
    taker_fee_bps: Decimal | None = None
    spread_bps: Decimal = Decimal("0")
    slippage_model: Literal["LINEAR", "SQRT_IMPACT"] = "LINEAR"
    impact_coeff: Decimal | None = None
    funding_bps_per_period: Decimal = Decimal("0")

    @model_validator(mode="after")
    def _default_maker_taker_fees(self) -> CostModel:
        if self.maker_fee_bps is None:
            self.maker_fee_bps = self.fee_bps
        if self.taker_fee_bps is None:
            self.taker_fee_bps = self.fee_bps
        return self

    def cost_model_hash(self) -> str:
        """L25 -- sha256 hex of the canonical JSON (R-01 `canonical_json`) of
        every field.

        Reuses `src.core.risk.hashing` instead of inventing a new hash
        recipe -- the same "identical values yield identical bytes" contract
        as DSL-12 `script_hash` and BT-9 `config_hash`.
        `src.core.portfolio.config.CostModelRef` stores this value as its
        reference key."""
        return sha256_hex(canonical_json(self.model_dump(mode="python")))


class BacktestConfig(BaseModel):
    """재생 1회 실행에 필요한 모든 입력 — 105번 원칙에 따라 실행 전
    고정(pinned)된다. `warmup_bars`는 지표가 유효해지기 전 구간을
    신호평가에서 제외하는 데 쓴다(예: SMA(20)이면 최소 20).

    v2 (MINOR): adds reproducibility inputs (`seed`, `timeframe`,
    `data_snapshot_hash`) and fill/survivorship policy switches. All get
    defaults so existing callers' instances stay valid (107 §3.3 MINOR
    rule)."""

    strategy_id: str
    strategy_version: str
    initial_equity: Decimal
    cost_model: CostModel
    warmup_bars: int = Field(ge=0)
    periods_per_year: int = Field(gt=0)
    """Sharpe/Sortino 연환산 계수 — bar timeframe에 맞춰 호출자가 지정한다
    (예: 일봉이면 252, 1시간봉이면 365*24). 엔진이 timeframe 문자열을
    파싱해 추측하지 않는다 — 추측이 틀리면 조용히 틀린 지표를 만들기
    때문에(46번 §2 "unit/annualization convention" 필수 표기 원칙)."""
    seed: int = 0
    timeframe: str | None = None
    data_snapshot_hash: str | None = None
    fill_policy: Literal["NEXT_OPEN", "NEXT_OPEN_WITH_GAP_CHECK"] = "NEXT_OPEN"
    survivorship_policy: Literal["UNIVERSE_SNAPSHOT_REQUIRED"] = (
        "UNIVERSE_SNAPSHOT_REQUIRED"
    )

    def config_hash(self) -> str:
        """Reuses R-01 -- canonical hash of every field, including `cost_model`."""
        return sha256_hex(canonical_json(self.model_dump(mode="python")))


class SimulatedFill(BaseModel):
    bar_index: int
    timestamp: datetime
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: Decimal
    fee: Decimal
    slippage_cost: Decimal


class EquityPoint(BaseModel):
    bar_index: int
    timestamp: datetime
    equity: Decimal
    drawdown_pct: Decimal


class BacktestMetrics(BaseModel):
    """76번 "bare float 성과값 금지" 원칙 — 모든 값에 단위/기간을
    필드명으로 명시한다. `sharpe_ratio`/`sortino_ratio`는 표본이 2개
    미만이거나 표준편차가 0이면 계산 불가라 None(46번이 요구하는
    "한계·가정"의 최소 구현 — 조용히 0을 내지 않는다).

    v2 (MINOR): adds gross/net split, cost totals, calmar, exposure time,
    annualization factor and the reporting `basis`. Stays `None` until
    L31 (`compute_metrics.py`) fills it in, keeping "not yet computed"
    distinct from zero."""

    period_start: datetime
    period_end: datetime
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    win_rate_pct: Decimal | None
    total_trades: int
    turnover: Decimal
    gross_return_pct: Decimal | None = None
    net_return_pct: Decimal | None = None
    total_fees: Decimal | None = None
    total_slippage: Decimal | None = None
    total_funding: Decimal | None = None
    calmar_ratio: Decimal | None = None
    exposure_time_pct: Decimal | None = None
    annualization: int | None = None
    basis: Literal["PAPER_SIM"] = "PAPER_SIM"


class BacktestResult(BaseModel):
    config: BacktestConfig
    fills: list[SimulatedFill]
    equity_curve: list[EquityPoint]
    metrics: BacktestMetrics
    warnings: list[str] = Field(default_factory=list)
