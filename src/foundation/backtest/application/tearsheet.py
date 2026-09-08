"""BT-12 성과 리포트(tearsheet) — performance 계약 재사용, 스냅샷 결정론.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.5
BT-12(선행 BT-10). `compute_metrics()`(BT-10, application/compute_metrics.py)가
이미 계산한 `BacktestMetrics`를 그대로 소비하고, `src/foundation/performance/`
계약 DTO(`PerformanceMethodologyView`)를 조합해 리포트 뷰를 만든다 — §C
벤더/기존 컨텍스트 우선 원칙에 따라 sharpe/sortino/MDD/승률/수익률 산식을
다시 구현하지 않는다(호출·포맷만). HTTP 라우터·DB 테이블은 이 리프
스콥 밖이다.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from src.foundation.backtest.domain.models import BacktestResult
from src.foundation.performance.contracts.v1 import PerformanceMethodologyView
from src.foundation.performance.domain.methodology import methodology_hash
from src.foundation.performance.domain.models import Methodology

SCHEMA_VERSION = "v1"

# 백테스트는 단일 재생 실행(현금흐름 없음)이라 TWR/MWR 산식 자체를 쓰지 않지만,
# 방법론 계약 스키마(버전·해시)는 성과 컨텍스트와 동일 규약을 그대로 재사용한다
# (domain/methodology.py DEFAULT_METHODOLOGY와 동일 상수, periods_per_year만
# 백테스트 config에서 가져온다).
_METHODOLOGY_VERSION = "pm-v1"
_TWR_METHOD = "PERIOD_LINKED_CASHFLOW_AT_START"
_MWR_METHOD = "IRR_BISECTION"
_RISK_FREE_RATE_PCT = Decimal("0")


class TearsheetView(BaseModel):
    """BT-12 리포트 스냅샷 — `compute_metrics()` 출력값의 순수 조합·포맷.

    모든 수치 필드는 `BacktestResult`/`BacktestMetrics`에서 그대로 옮겨온
    값이며(재계산 없음), Decimal은 pydantic v2 기본 동작으로 JSON 직렬화 시
    문자열이 된다."""

    strategy_id: str
    strategy_version: str
    period_start: datetime
    period_end: datetime
    initial_equity: Decimal
    final_equity: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    win_rate_pct: Decimal | None
    total_trades: int
    turnover: Decimal
    methodology: PerformanceMethodologyView
    warnings: list[str]
    schema_version: str = SCHEMA_VERSION


def build_tearsheet(result: BacktestResult) -> TearsheetView:
    """`BacktestResult`를 성과 리포트 뷰로 포맷한다 — 지표는 재계산하지 않는다."""

    metrics = result.metrics  # BT-10 compute_metrics()가 이미 계산한 값
    config = result.config

    methodology = Methodology(
        version=_METHODOLOGY_VERSION,
        methodology_hash="",
        twr_method=_TWR_METHOD,
        mwr_method=_MWR_METHOD,
        risk_free_rate_pct=_RISK_FREE_RATE_PCT,
        periods_per_year=config.periods_per_year,
    )
    methodology_view = PerformanceMethodologyView(
        version=methodology.version,
        methodology_hash=methodology_hash(methodology),  # 기존 함수 재사용, 재구현 아님
        twr_method=methodology.twr_method,
        mwr_method=methodology.mwr_method,
        risk_free_rate_pct=methodology.risk_free_rate_pct,
        periods_per_year=methodology.periods_per_year,
    )

    return TearsheetView(
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        period_start=metrics.period_start,
        period_end=metrics.period_end,
        initial_equity=config.initial_equity,
        final_equity=result.equity_curve[-1].equity,
        total_return_pct=metrics.total_return_pct,
        max_drawdown_pct=metrics.max_drawdown_pct,
        sharpe_ratio=metrics.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio,
        win_rate_pct=metrics.win_rate_pct,
        total_trades=metrics.total_trades,
        turnover=metrics.turnover,
        methodology=methodology_view,
        warnings=list(result.warnings),
    )
