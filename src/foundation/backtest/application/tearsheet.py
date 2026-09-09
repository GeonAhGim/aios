"""BT-12 — 백테스트 성과 리포트 뷰.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 `application/tearsheet.py`(성과 리포트, 기존 performance 계약
재사용), §9.5 BT-12(선행 BT-10).

지표 산식(Sharpe/Sortino/MDD/승률/수익률)을 다시 구현하지 않는다 —
BT-10(`compute_metrics.py`)이 이미 계산해 넣은 `BacktestResult.metrics`를
그대로 읽어 `src/foundation/performance/contracts/v1.py`의 `ReturnValue`
DTO 모양으로 옮겨 담을 뿐이다(벤더/기존 컨텍스트 우선 원칙, §C).

순수 함수 — I/O·시계·난수 접근이 없다. 같은 `BacktestResult` 입력이면
바이트 동일한 `TearsheetView`를 낸다(리프 DoD "리포트 스냅샷 결정론").
HTTP 라우터·DB 저장(리포트 영속화)은 이 리프의 범위 밖이다(후속 리프).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from src.foundation.backtest.domain.models import BacktestResult
from src.foundation.performance.contracts.v1 import SCHEMA_VERSION, ReturnValue

__all__ = ["TearsheetView", "build_tearsheet"]


class TearsheetView(BaseModel):
    """BT-12 리포트 스냅샷. `Decimal` 필드는 pydantic v2 JSON 모드가
    기본으로 문자열 직렬화한다(`model_dump(mode="json")`) — 별도 인코더가
    필요 없다."""

    strategy_id: str
    strategy_version: str
    period_start: datetime
    period_end: datetime
    initial_equity: Decimal
    final_equity: Decimal
    total_return: ReturnValue
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    win_rate_pct: Decimal | None
    total_trades: int
    turnover: Decimal
    basis: Literal["PAPER_SIM"]
    config_hash: str
    limitations: list[str]
    schema_version: str = SCHEMA_VERSION


def build_tearsheet(result: BacktestResult) -> TearsheetView:
    """`BacktestResult`(run_backtest.py 산출물, `metrics`는 이미 BT-10이
    계산 완료) → 리포트 뷰. 빈 `equity_curve`는 호출자 버그 신호라 조용히
    0을 채우지 않고 거부한다(compute_metrics.py와 같은 원칙)."""
    if not result.equity_curve:
        raise ValueError("빈 equity_curve로는 리포트를 만들 수 없습니다.")

    metrics = result.metrics
    config = result.config
    total_return = ReturnValue(
        value_pct=metrics.total_return_pct,
        basis="NET",  # 체결 fee/slippage가 이미 fills에 반영된 equity 기준(76번 §2)
        method="TWR",
        period_start=metrics.period_start,
        period_end=metrics.period_end,
        annualized=False,
        periods_per_year=config.periods_per_year,
    )
    return TearsheetView(
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        period_start=metrics.period_start,
        period_end=metrics.period_end,
        initial_equity=config.initial_equity,
        final_equity=result.equity_curve[-1].equity,
        total_return=total_return,
        max_drawdown_pct=metrics.max_drawdown_pct,
        sharpe_ratio=metrics.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio,
        win_rate_pct=metrics.win_rate_pct,
        total_trades=metrics.total_trades,
        turnover=metrics.turnover,
        basis=metrics.basis,
        config_hash=config.config_hash(),
        limitations=list(result.warnings),
    )
