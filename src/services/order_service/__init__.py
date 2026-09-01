"""FD-4 주문 처리(전송 계층) — src/services/order_service/.

판단(주문을 낼지 말지, 얼마나)은 FD-8(FROZEN-PAPER-ONLY)의 책임이다.
이 패키지는 "이미 승인된 주문을 어떻게 안전하게 전송·추적하는가"만
다룬다(8.2-A Master Authority 경계선).
"""
from __future__ import annotations

from src.services.order_service.cancel import OrderCancelError, cancel_order
from src.services.order_service.modify import OrderModifyError, modify_order
from src.services.order_service.reconcile import resolve_unknown
from src.services.order_service.submit import OrderSubmissionError, apply_fill, submit_order

__all__ = [
    "OrderCancelError",
    "OrderModifyError",
    "OrderSubmissionError",
    "apply_fill",
    "cancel_order",
    "modify_order",
    "resolve_unknown",
    "submit_order",
]
