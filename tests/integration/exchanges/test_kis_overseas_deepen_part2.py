"""task-2772 DEEPEN 분할(part2): 해외주식 전용 메서드의 live 설정 차단 확인.

500줄 ratchet 때문에 옮겼다(ruff format 일괄 적용 뒤 원 파일이 512줄이 됐다).
place_overseas_order/cancel_overseas_order가 live 설정 서브클래스에서 막히는지 본다.
공유 헬퍼는 원 파일(test_kis_overseas_deepen.py)에서 가져온다.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import (
    FrozenZonePaperAdapterBlockedError,
)
from src.data.models.base import AssetClass
from src.exchanges.kis.adapter import KISAdapter
from tests.integration.exchanges.test_kis_overseas_deepen import (
    _fast_success_handler,
    _instant_sleep,
    _order,
)


async def test_live_configured_subclass_is_blocked_from_overseas_order_mutation() -> None:
    """방어 심화 검증 — 정상 경로로는 절대 만들어질 수 없는 상태
    (`is_paper_trading=False`)를 강제로 재현해도 `require_paper_sandbox`가
    `place_overseas_order`/`cancel_overseas_order`를 실제로 막는지
    확인한다(파일 스콥이 place_order 분기만 다루던 task-1786과 달리,
    해외주식 전용 메서드 자체에 대한 방어를 직접 확인)."""

    class _LiveConfiguredKISAdapter(KISAdapter):
        @property
        def is_paper_trading(self) -> bool:
            return False

    captured: list[httpx.Request] = []
    adapter = _LiveConfiguredKISAdapter(
        "app",
        "secret",
        "12345678",
        "01",
        is_paper_trading=True,  # 생성자 값은 무시되도록 프로퍼티를 오버라이드했다
        http_client=httpx.AsyncClient(
            base_url="https://openapivts.koreainvestment.com:29443",
            transport=httpx.MockTransport(_fast_success_handler(captured)),
        ),
        sleep_fn=_instant_sleep,
    )

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.place_overseas_order(_order(AssetClass.US_EQUITY, "AAPL"), "NASD")

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.cancel_overseas_order("ORG:1", "AAPL", "NASD", original_quantity=Decimal("1"))

    assert captured == []  # 두 호출 다 HTTP 요청 자체가 나가지 않았다
