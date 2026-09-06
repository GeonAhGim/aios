"""스톱 주문 트리거 판정(순수)(L4 명세 §9 EM-19).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-19,
ADR-2026-09-06-G §9("주문 유형이 MARKET·LIMIT뿐 — 스톱·스톱리밋·OCO·
트레일링 없음. EMSX·Charles River는 이를 알고리즘이 아니라 기본 주문
유형으로 다룬다").

트리거된 뒤의 실제 체결가는 이 함수의 책임이 아니다(venue 전송·
fill_normalizer.py) — 이 모듈은 "이 틱에서 트리거 조건을 만족했는가"만
판정한다. 백테스트 쪽 동형 판정(`foundation/backtest/domain/fill/
order_types.py` BT-6)은 봉의 고저(bar_low/bar_high)로 판정하지만,
라이브는 매 틱이 단일가(last_price)뿐이라 같은 부등식을 틱 기준으로
다시 쓴다 — 두 모듈은 서로 다른 계층(백테스트 vs 라이브)의 독립 판정이라
서로 임포트하지 않는다.
"""
from __future__ import annotations

from decimal import Decimal

from src.data.models.trading import OrderSide


def _reject_non_positive(value: Decimal, name: str) -> None:
    if value.is_nan() or value <= 0:
        raise ValueError(f"{name}는 0보다 커야 합니다: {value}")


def is_stop_triggered(*, side: OrderSide, trigger_price: Decimal, last_price: Decimal) -> bool:
    """매수 스탑(돌파매수·숏커버)은 마지막 체결가가 트리거가 이상으로
    오르면, 매도 스탑(손절)은 트리거가 이하로 내리면 발동한다 — 경계값
    포함이라 "정확히 트리거가에" 닿는 틱도 트리거된다."""
    _reject_non_positive(trigger_price, "trigger_price")
    _reject_non_positive(last_price, "last_price")
    if side == OrderSide.BUY:
        return last_price >= trigger_price
    return last_price <= trigger_price
