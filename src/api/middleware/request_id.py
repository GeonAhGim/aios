"""요청 상관관계 ID 미들웨어 — 요청 하나의 전체 처리 과정(라우터→서비스
→로그)을 하나의 ID로 엮는다.

동작: 들어온 요청의 `X-Request-ID` 헤더가 있으면 그대로 쓰고, 없으면
새로 생성한다. 요청 처리 동안 contextvar(core/logging/request_context.py —
core 계층에 두는 이유는 그 모듈의 docstring 참조)에 담아두고,
JSONLinesFormatter가 로그 한 줄마다 호출자가 correlation_id를 안 넘겼을
때의 기본값으로 이 값을 자동으로 채운다. 응답에도 같은 값을
`X-Request-ID` 헤더로 그대로 돌려줘, 클라이언트가 장애 신고 시 이 값
하나로 서버 로그를 바로 찾을 수 있게 한다.

`app.add_middleware(RequestIdMiddleware)` 등록은 main.py 앱 조립 지점에서
한다(이 파일은 미들웨어 자체만 제공).
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.logging.request_context import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
