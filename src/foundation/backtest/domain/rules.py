"""Backtest Simulation Engine 순수 규칙 함수 — DB/HTTP 없이 단위테스트 가능해야 한다.

Spec: AIOSproject 109번 §5 — look-ahead bias 방지가 이 엔진의 핵심 불변조건이다.
"""
from __future__ import annotations

from src.foundation.backtest.domain.models import CostModel


def is_look_ahead_safe(*, signal_bar_index: int, fill_bar_index: int) -> bool:
    """신호가 발생한 bar의 정보로 같은 bar나 과거 bar에 체결시키면
    미래 정보를 쓴 것이다(look-ahead bias) — 반드시 signal_bar_index보다
    나중 bar에서만 체결된다."""
    return fill_bar_index > signal_bar_index


def warn_if_zero_cost(cost_model: CostModel) -> str | None:
    """0 비용 자체를 금지하지는 않는다(의도적으로 비용을 배제한 민감도
    분석 시나리오가 있을 수 있음 — 46번 §2 Robustness 행) — 다만 사용자가
    "비용 없는 수익만 제시" 함정에 빠지지 않도록 결과에 경고를 남긴다."""
    if cost_model.fee_bps == 0 and cost_model.slippage_bps == 0:
        return (
            "cost_model이 fee_bps=0, slippage_bps=0입니다 — 이 결과의 수익률은 "
            "거래비용을 전혀 반영하지 않았습니다(46번 §2 Backtest 행 필수 공시)."
        )
    return None


def has_enough_warmup(*, total_bars: int, warmup_bars: int) -> bool:
    """warmup 구간을 빼고 나면 평가할 bar가 하나도 안 남는 설정을
    조용히 통과시키지 않는다."""
    return total_bars > warmup_bars
