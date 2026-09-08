"""9.2/9.5 — Griefing 방어: 계좌 손실이 시장 전체 급변과 상관되는지 판정.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.3 (`market_correlation.py`),
§4.3 liquidation_request 상태표, RED_TEAM_FINDINGS RTF-03(task-1568).

순수 함수 하나. 여러 심볼(basket)의 수익률을 받아 "동시에 유의미하게
하락했는가"만 판정한다 — 상관계수 산식(피어슨 등)은 `risk_stats/
correlation_matrix.py`(R-18~20)의 몫이고 여기서 재구현하지 않는다.
이 리프는 그 결과를 쓰는 쪽(watchdog)이 필요로 하는 "바스켓 급변 여부"라는
더 좁은 이진 판정만 담당한다.
"""
from __future__ import annotations

from decimal import Decimal


def is_market_wide_move(
    basket_returns: dict[str, Decimal],
    *,
    account_loss_pct: Decimal,
    min_symbols: int = 3,
    move_threshold_pct: Decimal,
) -> bool | None:
    """basket이 `min_symbols` 미만이면 판정 불가 → `None`(호출부는 이를
    "조작 의심"과 동일하게 안전한 쪽으로 취급한다 — `watchdog.decide` 참조).

    계좌 손실(`account_loss_pct`, 항상 0 이상)이 실제로 존재할 때만 상관을
    따진다 — 손실이 없으면 "시장 급변과 손실이 상관됐는지" 자체가 성립하지
    않으므로 False. basket 과반수 심볼이 `move_threshold_pct` 이상 동시에
    하락했으면 시장 전체 급변으로 판정한다(단일·소수 심볼 급변은 고립된
    손실 — 조작 의심 쪽으로 남긴다).
    """
    if len(basket_returns) < min_symbols:
        return None
    if account_loss_pct <= 0:
        return False

    declined = sum(1 for r in basket_returns.values() if r <= -move_threshold_pct)
    return declined * 2 > len(basket_returns)
