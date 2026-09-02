"""77번 §7 rollout 1단계 "fake adapter" — 실 provider 통합은 이 리프의 스콥이
아니다(71번 §6 "LIVE/provider/custody 관련 PR은 60~63번 승인 게이트 이후").
"""
from __future__ import annotations

from uuid import uuid4

from src.foundation.paper_control.ports.paper_adapter import (
    PaperExecutionContext,
    PaperOrderAck,
)


class FakePaperExecutionAdapter:
    def __init__(self, *, fail_submit: bool = False) -> None:
        self._fail_submit = fail_submit

    async def submit_paper_intent(
        self, context: PaperExecutionContext, sequence: int
    ) -> PaperOrderAck:
        if self._fail_submit:
            raise ConnectionError("fake paper adapter: submit 실패(시뮬레이션)")
        return PaperOrderAck(provider_order_ref=f"fake-paper-order-{uuid4().hex[:8]}")

    async def cancel_paper_order(
        self, context: PaperExecutionContext, provider_order_ref: str
    ) -> None:
        return None

    async def fetch_paper_state(self, context: PaperExecutionContext) -> str:
        return "OK"
