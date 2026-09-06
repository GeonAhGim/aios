"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- 생성 mixin의 어댑터 인터페이스.

`scripts/kis_generate_adapters.py`가 생성한다 -- 손으로 수정하지 말 것. Protocol을
베이스 클래스로 명시 상속하면(구조적 타이핑) `self._request`/`self._is_paper_trading`
같은 크로스-mixin 접근에 `# type: ignore[attr-defined]`가 필요 없다 -- 실제 구현은
`KISAdapter`의 다른 mixin(`_KISHTTPClient`/`KISWebSocketMixin`)이 MRO 앞쪽에서
채운다(PLT-40 type-ignore 예산 래칫을 건드리지 않기 위한 설계 선택).
"""
from __future__ import annotations

from typing import Any, Protocol


class _KISRestHost(Protocol):
    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class _KISWsHost(Protocol):
    _is_paper_trading: bool

    async def get_ws_approval_key(self) -> str: ...
