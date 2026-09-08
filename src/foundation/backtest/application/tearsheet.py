"""BT-12 application/tearsheet.py — 성과 리포트 스냅샷.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9
BT-12(선행 BT-10=task-1504 `compute_metrics`, done).

`BacktestResult.metrics`(BT-10 `compute_metrics()`가 이미 계산한 값)를
재계산하지 않고 그대로 소비해 `src/foundation/performance/` 계약
DTO(`PerformanceMethodologyView`)·해시 함수(`methodology_hash()`)와
조합해 읽기 전용 리포트 뷰를 만든다(§C 벤더/기존 컨텍스트 우선 원칙 —
sharpe·sortino·MDD·승률·수익률 산식을 다시 구현하지 않는다, 호출·포맷만
한다). HTTP 라우터·DB 테이블은 이 리프의 스콥이 아니다.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from src.foundation.backtest.domain.models import BacktestResult
from src.foundation.performance.contracts.v1 import PerformanceMethodologyView
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY, methodology_hash

SCHEMA_VERSION = "v1"


class TearsheetMetricsView(BaseModel):
    """`BacktestMetrics`를 그대로 옮겨 담는다 — 산식 재구현 없이 포맷만."""

    period_start: datetime
    period_end: datetime
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    win_rate_pct: Decimal | None
    total_trades: int
    turnover: Decimal


class TearsheetView(BaseModel):
    """BT-12 리포트 스냅샷 — DB/HTTP 없는 순수 조합 결과.

    `model_dump_json()`은 pydantic v2 기본 동작으로 `Decimal` 필드를
    문자열로 직렬화한다(46번 "bare float 성과값 금지"와 동일 취지)."""

    schema_version: str = SCHEMA_VERSION
    strategy_id: str
    strategy_version: str
    initial_equity: Decimal
    metrics: TearsheetMetricsView
    methodology: PerformanceMethodologyView
    warnings: list[str]


def _methodology_view(*, periods_per_year: int) -> PerformanceMethodologyView:
    """`DEFAULT_METHODOLOGY`(performance/domain/methodology.py)를 재사용하되
    백테스트 config가 지정한 `periods_per_year`로 덮어쓴다 — 연환산 계수는
    호출부가 명시해야 한다는 원칙이 BT-10 `compute_metrics()`와 여기서도
    동일하게 적용된다. 해시는 값이 바뀌면 재계산해야 하므로 기존
    `methodology_hash()`를 다시 호출한다(재구현 아님)."""

    m = replace(DEFAULT_METHODOLOGY, periods_per_year=periods_per_year, methodology_hash="")
    m = replace(m, methodology_hash=methodology_hash(m))
    return PerformanceMethodologyView(
        version=m.version,
        methodology_hash=m.methodology_hash,
        twr_method=m.twr_method,
        mwr_method=m.mwr_method,
        risk_free_rate_pct=m.risk_free_rate_pct,
        periods_per_year=m.periods_per_year,
    )


def build_tearsheet(result: BacktestResult) -> TearsheetView:
    """`result.metrics`(BT-10이 이미 계산)를 그대로 소비한다 —
    sharpe/sortino/MDD/승률/수익률을 여기서 다시 계산하지 않는다."""

    m = result.metrics
    return TearsheetView(
        strategy_id=result.config.strategy_id,
        strategy_version=result.config.strategy_version,
        initial_equity=result.config.initial_equity,
        metrics=TearsheetMetricsView(
            period_start=m.period_start,
            period_end=m.period_end,
            total_return_pct=m.total_return_pct,
            max_drawdown_pct=m.max_drawdown_pct,
            sharpe_ratio=m.sharpe_ratio,
            sortino_ratio=m.sortino_ratio,
            win_rate_pct=m.win_rate_pct,
            total_trades=m.total_trades,
            turnover=m.turnover,
        ),
        methodology=_methodology_view(periods_per_year=result.config.periods_per_year),
        warnings=list(result.warnings),
    )
