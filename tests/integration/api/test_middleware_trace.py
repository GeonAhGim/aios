"""통합테스트 — PLT-05: `RequestContextMiddleware`가 `main.py`에 등록된 뒤
응답 헤더(`X-Request-ID`·`X-Trace-Id`)와 요청당 로그 1줄(108 §2 8필드)이 실제로
나오는지 확인한다.

기존 `RequestIdMiddleware` 단위테스트(tests/unit/api/middleware/test_request_id.py)
는 이 리프에서 손대지 않았다 — 그 파일이 무수정 통과하는 것도 이 리프의 DoD다.
이 파일은 그 위에 얹힌 X-Trace-Id·구조화 로그·traceparent 채택만 검증한다.
"""
from __future__ import annotations

import logging
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.middleware.request_context import TRACE_ID_HEADER
from src.api.middleware.request_id import REQUEST_ID_HEADER
from src.core.logging import fields as log_fields
from src.core.observability.context import current as current_request_context
from src.main import app

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False — test_auth_router.py와 동일 근거
        # (tests/unit/api/contracts/test_handlers.py 참조).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def test_response_carries_request_id_and_trace_id_headers(client):
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]
    assert _UUID_RE.match(response.headers[TRACE_ID_HEADER])


async def test_traceparent_header_trace_id_is_adopted(client):
    trace_id = uuid.uuid4()
    traceparent = f"00-{trace_id.hex}-0123456789abcdef-01"

    response = await client.get("/openapi.json", headers={"traceparent": traceparent})

    assert response.headers[TRACE_ID_HEADER] == str(trace_id)


async def test_malformed_traceparent_header_is_ignored(client):
    """형식이 아닌 traceparent를 보내도 요청이 실패하지 않고, 새 trace_id가
    생성될 뿐이다 — 클라이언트 입력을 신뢰하지 않는다는 negative case."""
    response = await client.get("/openapi.json", headers={"traceparent": "not-a-traceparent"})

    assert response.status_code == 200
    assert _UUID_RE.match(response.headers[TRACE_ID_HEADER])


class _StructuredCapture(logging.Handler):
    """`configure_logging`의 `QueueHandler`(비동기 리스너 스레드)를 거치지 않고,
    로그 호출 시점(=`RequestContextMiddleware`의 `with bind(...)` 블록 안)에
    동기적으로 `fields.from_record`를 호출해 그 순간의 `RequestContext`를
    정확히 캡처한다 — 리스너 스레드가 나중에(컨텍스트가 이미 풀린 뒤) 다시
    포맷팅하면서 생기는 이중 인코딩(task-845 QueueHandler.prepare()가 이미
    포맷된 문자열을 record.msg에 되먹임)을 피하기 위함이다."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        structured = log_fields.from_record(record, current_request_context())
        self.lines.append(structured.model_dump(mode="json"))


async def test_request_completion_logs_one_line_with_108_fields(client):
    capture = _StructuredCapture()
    root = logging.getLogger()
    root.addHandler(capture)
    try:
        response = await client.get("/openapi.json")
    finally:
        root.removeHandler(capture)

    assert response.status_code == 200
    completed = [line for line in capture.lines if line.get("event") == "http_request_completed"]
    assert len(completed) == 1

    line = completed[0]
    for field_name in log_fields.REQUIRED_FIELDS:
        assert field_name in line
    assert line["trace_id"] == response.headers[TRACE_ID_HEADER]
    assert isinstance(line["duration_ms"], int)
    assert line["extra"]["route"] == "/openapi.json"
    assert line["extra"]["status"] == 200
