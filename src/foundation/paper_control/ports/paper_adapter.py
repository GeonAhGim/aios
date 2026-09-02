"""77번 §4 paper adapter port — `submit_paper_intent`, `cancel_paper_order`,
`fetch_paper_state`만 노출한다. 일반/LIVE `ExchangeAdapter`를 받지 않고,
반드시 `PaperExecutionContext`(provenance 증명 포함)를 통해서만 호출된다.
빌드/DI 규칙(77번 §4)상 이 모듈은 live endpoint/credential provider를
로드하지 않는다 — 이 파일이 import하는 것은 fake adapter 하나뿐이다."""
from __future__ import annotations

from typing import Protocol

from src.foundation.paper_control.domain.models import AdapterProvenance


class PaperExecutionContext:
    def __init__(self, deployment_id: str, provenance: AdapterProvenance) -> None:
        self.deployment_id = deployment_id
        self.provenance = provenance


class PaperOrderAck:
    def __init__(self, provider_order_ref: str) -> None:
        self.provider_order_ref = provider_order_ref


class PaperExecutionAdapter(Protocol):
    async def submit_paper_intent(
        self, context: PaperExecutionContext, sequence: int
    ) -> PaperOrderAck: ...

    async def cancel_paper_order(
        self, context: PaperExecutionContext, provider_order_ref: str
    ) -> None: ...

    async def fetch_paper_state(self, context: PaperExecutionContext) -> str: ...
