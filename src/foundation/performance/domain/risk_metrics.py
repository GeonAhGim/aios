"""변동성·MDD·Sharpe·Calmar — 순수 함수.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6 — "backtest.
compute_metrics와 동일 정의를 공유하도록 순수 함수로 추출"(두 컨텍스트가
같은 산식을 따로 재구현해 슬며시 갈라지는 것을 막는다).

전부 기간 수익률 리스트를 입력으로 받는다 — annualization은 항상
`periods_per_year`를 명시적으로 요구한다(암묵적 가정 금지, 73번 §6·78번
"typed input" 원칙과 같은 정신)."""
from __future__ import annotations

from decimal import Decimal


def period_returns(values: list[Decimal]) -> list[Decimal]:
    """연속된 값 사이의 단순수익률. 밑변이 0인 구간은 정의 불가라 건너뛴다
    (0으로 나누기 대신 그 구간만 결측 처리 — 호출부가 원하면 결과 길이로
    결측 여부를 알 수 있다)."""
    returns: list[Decimal] = []
    for prev, cur in zip(values, values[1:], strict=False):
        if prev == 0:
            continue
        returns.append(cur / prev - 1)
    return returns


def annualized_vol(returns: list[Decimal], *, periods_per_year: int) -> Decimal | None:
    if len(returns) < 2:
        return None
    mean = sum(returns, Decimal(0)) / len(returns)
    variance = sum(((r - mean) ** 2 for r in returns), Decimal(0)) / (len(returns) - 1)
    return variance.sqrt() * Decimal(periods_per_year).sqrt()


def max_drawdown(values: list[Decimal]) -> Decimal | None:
    """양의 소수(0.15 = 15% 낙폭)로 돌려준다. 값이 없으면 정의할 수 없다."""
    if not values:
        return None
    peak = values[0]
    worst = Decimal(0)
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            drawdown = (peak - v) / peak
            if drawdown > worst:
                worst = drawdown
    return worst


def sharpe(
    returns: list[Decimal], *, rf: Decimal, periods_per_year: int
) -> Decimal | None:
    """`rf`는 기간당(연율 아님) 무위험수익률 — 연 무위험률을 쓰려면 호출부가
    `rf / periods_per_year`로 미리 나눠 넘긴다(암묵적 변환 금지)."""
    if len(returns) < 2:
        return None
    vol = annualized_vol(returns, periods_per_year=periods_per_year)
    if vol is None or vol == 0:
        return None
    excess = [r - rf for r in returns]
    mean_excess = sum(excess, Decimal(0)) / len(excess)
    annualized_mean_excess = mean_excess * periods_per_year
    return annualized_mean_excess / vol


def calmar(annualized_return: Decimal | None, mdd: Decimal | None) -> Decimal | None:
    if annualized_return is None or mdd is None or mdd == 0:
        return None
    return annualized_return / mdd
