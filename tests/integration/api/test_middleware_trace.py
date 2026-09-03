"""통합테스트 — PLT-05: `RequestContextMiddleware`가 `main.py`에 등록된 뒤
응답 헤더(`X-Request-ID`·`X-Trace-Id`)와 요청당 로그 1줄(108 §2 8필드)이 실제로
나오는지 확인한다.

기존 `RequestIdMiddleware` 단위테스트(tests/unit/api/middleware/test_request_id.py)
는 이 리프에서 손대지 않았다 — 그 파일이 무수정 통과하는 것도 이 리프의 DoD다.
이 파일은 그 위에 얹힌 X-Trace-Id·구조화 로그·traceparent 채택만 검증한다.

2차(PLT-07): `record_audit_log`(legacy `audit_log`)와 `record_command_event`
(`foundation_audit_event`)가 같은 요청 컨텍스트에서 같은 trace_id를 남기는지
실DB로 확인한다. `record_command_event`는 PLT-07 이전에는 호출마다
`uuid4()`로 새 trace_id를 만들어 상관관계가 끊겼었다(전수감사 §6) — 이제
`current().trace_id`를 읽는다. `bind(trace_id=...)`는 `RequestContextMiddleware`가
매 요청 진입 시 호출하는 것과 동일한 컨텍스트 바인딩 지점이므로, 한 요청 안에서
그 두 함수가 호출되는 상황을 그대로 재현한다.
"""
from __future__ import annotations

import logging
import os
import re
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.middleware.request_context import TRACE_ID_HEADER
from src.api.middleware.request_id import REQUEST_ID_HEADER
from src.core.logging import fields as log_fields
from src.core.logging.audit_log import record_audit_log
from src.core.observability.context import bind
from src.core.observability.context import current as current_request_context
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.record_command_event import record_command_event
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


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


async def test_audit_log_and_audit_event_share_trace_id_with_response_header(client, pool):
    """DoD(task-906): 한 요청에서 기록된 `audit_log` 행과 `audit_event` 행의
    trace_id가 서로 같고, 그 요청의 응답 헤더 `X-Trace-Id`와도 일치한다."""
    trace_id = uuid.uuid4()
    traceparent = f"00-{trace_id.hex}-0123456789abcdef-01"

    # RequestContextMiddleware가 이 trace_id를 채택해 응답 헤더로 왕복시킴을
    # 먼저 확인한다(test_traceparent_header_trace_id_is_adopted와 동일 근거) —
    # 즉 이 trace_id는 실제로 어떤 요청이 X-Trace-Id로 받을 수 있는 값이다.
    response = await client.get("/openapi.json", headers={"traceparent": traceparent})
    assert response.headers[TRACE_ID_HEADER] == str(trace_id)

    # bind(trace_id=...)는 RequestContextMiddleware가 요청 진입 시 호출하는 것과
    # 같은 지점 — 그 블록 안에서 audit_log·audit_event를 모두 기록해 "한 요청"
    # 안에서 두 쓰기가 일어나는 상황을 재현한다. action_type은 VARCHAR(50)이라
    # 짧게 고정하고, 이 테스트 실행분만 골라내는 유일 마커는 target_id(VARCHAR(100))에
    # 싣는다.
    marker = trace_id.hex
    aggregate_id = uuid.uuid4()
    async with pool.acquire() as conn:
        with bind(trace_id=trace_id):
            await record_audit_log(
                conn,
                actor_agent="test-suite",
                action_type="test.plt07.trace",
                target_type="test_marker",
                target_id=marker,
                decision_data={"ok": True},
            )
            event = await record_command_event(
                PostgresAuditEventRepository(pool),
                tenant_id=None,
                aggregate_type="test.plt07",
                aggregate_id=aggregate_id,
                action="trace_propagation",
                actor_subject_id=None,
            )

        audit_row = await conn.fetchrow(
            "SELECT trace_id FROM audit_log WHERE target_type = 'test_marker' AND target_id = $1 "
            "ORDER BY log_id DESC LIMIT 1",
            marker,
        )

    assert audit_row is not None
    assert audit_row["trace_id"] == trace_id
    assert event.trace_id == trace_id
    assert str(audit_row["trace_id"]) == response.headers[TRACE_ID_HEADER]
    assert str(event.trace_id) == response.headers[TRACE_ID_HEADER]


async def test_record_audit_log_defaults_trace_id_to_current_context_negative(client, pool):
    """negative: 호출부가 trace_id를 넘기지 않고(과거 시그니처 그대로 호출),
    컨텍스트도 바인딩하지 않으면 fallback(새 uuid4)이 쓰이고, 서로 다른
    두 호출은 서로 다른 trace_id를 남긴다 — "아무거나 같은 값"으로 우연히
    통과하는 거짓양성을 막는다."""
    marker_a = uuid.uuid4().hex
    marker_b = uuid.uuid4().hex
    async with pool.acquire() as conn:
        await record_audit_log(
            conn,
            actor_agent="test-suite",
            action_type="test.plt07.no_ctx",
            target_type="test_marker",
            target_id=marker_a,
            decision_data={},
        )
        await record_audit_log(
            conn,
            actor_agent="test-suite",
            action_type="test.plt07.no_ctx",
            target_type="test_marker",
            target_id=marker_b,
            decision_data={},
        )
        row_a = await conn.fetchrow(
            "SELECT trace_id FROM audit_log WHERE target_type = 'test_marker' AND target_id = $1",
            marker_a,
        )
        row_b = await conn.fetchrow(
            "SELECT trace_id FROM audit_log WHERE target_type = 'test_marker' AND target_id = $1",
            marker_b,
        )

    assert row_a["trace_id"] is not None
    assert row_b["trace_id"] is not None
    assert row_a["trace_id"] != row_b["trace_id"]
