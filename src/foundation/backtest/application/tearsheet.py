"""BT-12 — application/tearsheet.py: 성과 리포트 뷰 조립(스냅샷).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9
BT-12(선행 BT-10 `application/compute_metrics.py` 완료).

이 모듈은 지표를 다시 계산하지 않는다 — `compute_metrics()`(BT-10)가 이미
낸 `BacktestMetrics`를 그대로 받아, `src/foundation/performance/contracts/
v1.py`(FND-09 Performance Reporting 계약)의 `MoneyValue`/`ReturnValue` DTO에
호출·포맷만 해서 리포트 뷰로 조립한다(§C 벤더/기존 컨텍스트 우선 원칙 —
sharpe·sortino·MDD·승률·수익률 산식 병행 구현 금지). 매핑 방식은
`performance/application/statement_projection.py`(domain → contracts 변환을
한 지점에 모으는 기존 선례)를 그대로 따른다.

`currency`/`precision`은 `BacktestConfig`에 없다 — `MoneyValue`가 요구하는
이 두 값을 추측(예: 심볼 문자열 파싱)하지 않고 호출자가 명시하게 강제한다
(46번 §2 "unit/annualization convention" 필수 표기 원칙과 동일한 fail-closed
규율; `quick_backtest.py`가 `periods_per_year`를 추측하지 않는 것과 같다).

HTTP 라우터·DB 테이블은 이 리프의 범위 밖이다(§9 BT-12) — `TearsheetView`는
순수 조립 결과만 반환한다.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel

from src.foundation.backtest.domain.models import BacktestResult
from src.foundation.performance.contracts.v1 import MoneyValue, ReturnValue

SCHEMA_VERSION: Final[Literal["v1"]] = "v1"


class TearsheetView(BaseModel):
    """BT-12 리포트 스냅샷 — 필드 선언 순서가 곧 JSON 키 순서다(pydantic v2는
    선언 순서를 그대로 보존한다). `model_dump_json()`은 `Decimal`을 문자열로
    직렬화하는 pydantic v2 기본 동작을 그대로 쓴다 — 별도 인코더가 없다.
    같은 `BacktestResult` 입력이면 두 번 호출해도 바이트 동일(DoD (a)):
    전역 시계·난수·dict/set 순회가 없다."""

    schema_version: Literal["v1"] = SCHEMA_VERSION
    strategy_id: str
    strategy_version: str
    period_start: datetime
    period_end: datetime
    initial_equity: MoneyValue
    final_equity: MoneyValue
    total_return: ReturnValue
    risk: dict[str, Decimal | None]
    """`PerformanceStatementView.risk`와 같은 형태 — sharpe/sortino/mdd_pct.
    계산 불가(compute_metrics가 None을 낸 경우)는 그대로 None을 전달한다
    (76번 "bare float 성과값 금지" — 조용히 0으로 바꾸지 않는다)."""
    win_rate_pct: Decimal | None
    total_trades: int
    turnover: Decimal
    warnings: list[str]


def build_tearsheet(result: BacktestResult, *, currency: str, precision: int) -> TearsheetView:
    """`BacktestResult`(compute_metrics가 이미 채운 `metrics`)를 리포트 뷰로
    조립한다. `currency`/`precision`은 이 백테스트 계정 화폐 표기 규약 —
    호출자가 명시적으로 넘긴다(추측 금지, docstring 상단 참조)."""
    metrics = result.metrics
    config = result.config
    initial_amount = config.initial_equity
    final_amount = result.equity_curve[-1].equity

    initial_equity = MoneyValue(
        amount=initial_amount,
        currency=currency,
        precision=precision,
        as_of=metrics.period_start,
        state="FINAL",
    )
    final_equity = MoneyValue(
        amount=final_amount,
        currency=currency,
        precision=precision,
        as_of=metrics.period_end,
        state="FINAL",
    )
    total_return = ReturnValue(
        value_pct=metrics.total_return_pct,
        basis="NET",
        # 백테스트 기간 중 외부 현금흐름(입출금)이 없으므로 TWR=MWR로
        # 수렴한다 — performance/domain/twr.py의 "현금흐름 없는 기간"과
        # 같은 전제로 TWR을 표기(성과 계약 §3.4).
        method="TWR",
        period_start=metrics.period_start,
        period_end=metrics.period_end,
        annualized=False,
        periods_per_year=config.periods_per_year,
    )
    risk = {
        "sharpe": metrics.sharpe_ratio,
        "sortino": metrics.sortino_ratio,
        "mdd_pct": metrics.max_drawdown_pct,
    }

    return TearsheetView(
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        period_start=metrics.period_start,
        period_end=metrics.period_end,
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return=total_return,
        risk=risk,
        win_rate_pct=metrics.win_rate_pct,
        total_trades=metrics.total_trades,
        turnover=metrics.turnover,
        warnings=list(result.warnings),
    )


__all__ = ["SCHEMA_VERSION", "TearsheetView", "build_tearsheet"]
