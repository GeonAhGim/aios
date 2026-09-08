"""BT-12 §9 build_tearsheet() — 성과 리포트 스냅샷(결정론).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9 BT-12
(선행 BT-10=task-1504 done).

compute_metrics()(BT-10, application/compute_metrics.py)가 이미 계산한
`BacktestResult.metrics`를 그대로 소비한다 — sharpe/sortino/MDD/승률/수익률
산식을 이 파일에서 다시 구현하지 않는다(§C 벤더/기존 컨텍스트 우선 원칙,
호출·포맷만 한다). 방법론 메타데이터는 src/foundation/performance/ 계약
(`PerformanceMethodologyView`)과 domain/methodology.py의
`methodology_hash()`를 그대로 호출해 재사용한다(재구현 금지).

HTTP 라우터·DB 테이블은 이 리프의 스콥이 아니다 — 반환은 pydantic DTO
(`schema_version='v1'`) 하나뿐이다.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from src.foundation.backtest.domain.models import BacktestResult
from src.foundation.performance.contracts.v1 import SCHEMA_VERSION, PerformanceMethodologyView
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY, methodology_hash
from src.foundation.performance.domain.models import Methodology


def _methodology_view(*, periods_per_year: int) -> PerformanceMethodologyView:
    """백테스트 `periods_per_year`에 맞춘 방법론 뷰.

    `DEFAULT_METHODOLOGY`(pm-v1)와 연환산 계수가 같으면 그 해시를 그대로
    쓴다. 다르면(예: 시간봉 365*24) `methodology_hash()`를 재호출해 새
    해시를 구한다 — 해시 산식 자체는 여기서 절대 다시 만들지 않는다(BT-12
    §C, methodology.py L23 methodology_hash가 유일한 산식 소유자)."""
    if periods_per_year == DEFAULT_METHODOLOGY.periods_per_year:
        resolved: Methodology = DEFAULT_METHODOLOGY
    else:
        provisional = replace(
            DEFAULT_METHODOLOGY, periods_per_year=periods_per_year, methodology_hash=""
        )
        resolved = replace(provisional, methodology_hash=methodology_hash(provisional))
    return PerformanceMethodologyView(
        version=resolved.version,
        methodology_hash=resolved.methodology_hash,
        twr_method=resolved.twr_method,
        mwr_method=resolved.mwr_method,
        risk_free_rate_pct=resolved.risk_free_rate_pct,
        periods_per_year=resolved.periods_per_year,
    )


class TearsheetView(BaseModel):
    """1회 백테스트 결과 리포트 뷰 — DB 테이블/HTTP 응답 스키마는 이 리프의
    스콥이 아니다(BT-12 DoD). 모든 지표 필드는 BacktestMetrics(BT-10)를
    그대로 옮긴 값이다 — 여기서 재계산하지 않는다."""

    strategy_id: str
    strategy_version: str
    period_start: datetime
    period_end: datetime
    initial_equity: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    win_rate_pct: Decimal | None
    total_trades: int
    turnover: Decimal
    total_fills: int
    warnings: list[str]
    methodology: PerformanceMethodologyView
    schema_version: str = SCHEMA_VERSION


def build_tearsheet(result: BacktestResult) -> TearsheetView:
    """BacktestResult → TearsheetView. 지표는 `result.metrics`(BT-10 산출물)
    를 그대로 옮긴다 — 여기서 sharpe/sortino/MDD 등을 재계산하지 않는다."""
    metrics = result.metrics
    return TearsheetView(
        strategy_id=result.config.strategy_id,
        strategy_version=result.config.strategy_version,
        period_start=metrics.period_start,
        period_end=metrics.period_end,
        initial_equity=result.config.initial_equity,
        total_return_pct=metrics.total_return_pct,
        max_drawdown_pct=metrics.max_drawdown_pct,
        sharpe_ratio=metrics.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio,
        win_rate_pct=metrics.win_rate_pct,
        total_trades=metrics.total_trades,
        turnover=metrics.turnover,
        total_fills=len(result.fills),
        warnings=list(result.warnings),
        methodology=_methodology_view(periods_per_year=result.config.periods_per_year),
    )
