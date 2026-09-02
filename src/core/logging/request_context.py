"""요청 상관관계 ID를 담는 contextvar — core 계층에 둔다.

`src/api/middleware/request_id.py`가 이 값을 설정하고, `schema.py`의
JSONLinesFormatter가 이 값을 읽는다. core가 api를 참조하면 계층 역전이라
(core는 api보다 낮은 레벨) contextvar 자체는 여기 core 쪽에 두고, api
미들웨어가 이걸 import해서 쓰는 방향으로만 의존한다.
"""
from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_current_request_id() -> str | None:
    """요청 컨텍스트 밖(예: 백그라운드 루프)에서 호출되면 None — 그
    자체가 "이 로그는 특정 HTTP 요청에 속하지 않는다"는 정직한 신호다."""
    return request_id_var.get()
