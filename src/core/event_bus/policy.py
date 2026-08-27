"""4.2 — HandlerCriticality, EventBusPolicy.

Spec: 05_communication_architecture_v1.2.md#§5.5
"""
from __future__ import annotations

from enum import Enum


class HandlerCriticality(str, Enum):
    """모든 handler를 동일하게 log_and_continue로 취급하면, 상태 변경(포지션
    갱신 등) handler의 실패까지 조용히 넘어가 내부 상태 불일치가 실시간으로
    드러나지 않는 위험이 있다. Handler 등록 시 이 값을 명시하도록 강제한다."""

    SAFE = "SAFE"  # 실패해도 시스템 상태에 영향 없음(예: 로깅용 구독자)
    CRITICAL = "CRITICAL"  # 실패 시 상태 불일치 가능(예: 포지션/잔고 갱신)


class EventBusPolicy:
    ON_HANDLER_ERROR = {
        HandlerCriticality.SAFE: "log_and_continue",
        HandlerCriticality.CRITICAL: "escalate_and_retry",
    }
