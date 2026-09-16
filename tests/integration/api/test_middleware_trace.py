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

3차(task-3149 DEEPEN): 원 리프(commit 40f8586)는 happy-path/행위 확인뿐이라
negative<3·실패 주입 0·수치 성능 단언 0·게이트 적색 재현 0으로 D2 하한
미달이었다. 새 규칙 추가 없이 이 리프의 증빙만 보강한다 — `tenant_binding.py`의
`rebind_tenant`(원래 "후속 리프가 배선할 신규 유틸"이라 전용 테스트 0건)에
negative 3건, `RequestContextMiddleware.dispatch`를 직접 단위 호출해 실패 주입
1건과 108 §8 "미들웨어 오버헤드 p95 < 1 ms" 성능 예산 단언 1건, 그 단언이
tautology가 아님을 증명하는 게이트 적색 재현 1건을 추가한다.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from types import SimpleNamespace

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import Response

from src.api.middleware import request_context as request_context_module
from src.api.middleware.request_context import TRACE_ID_HEADER, RequestContextMiddleware
from src.api.middleware.request_id import REQUEST_ID_HEADER
from src.core.logging import fields as log_fields
from src.core.logging.audit_log import record_audit_log
from src.core.observability import context as observability_context
from src.core.observability.context import bind
from src.core.observability.context import current as current_request_context
from src.core.observability.metric_names import AUTH_TENANT_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics import NullMetrics, set_metrics
from src.core.observability.tenant_binding import rebind_tenant
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.trust.contracts.v1 import TenantContext
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


# ---------------------------------------------------------------------------
# task-3149 DEEPEN — tenant_binding.rebind_tenant negative tests.
#
# rebind_tenant는 원 리프에서 "후속 리프가 배선할 신규 유틸"로 남아 전용
# 테스트가 0건이었다. INVARIANTS I1(스펙 446행)은 "한 trace_id에 둘 이상의
# tenant_id가 관측되지 않는다"를 fail-open(요청은 진행) + warn + 카운터로
# 강제하라고 규정한다 — 그 세 경로(최초 바인딩=불일치 아님, 반복 동일
# tenant=불일치 아님, 실제 불일치=카운터+경고+fail-open)를 negative로 고정한다.
# ---------------------------------------------------------------------------


class _SpyMetrics(NullMetrics):
    """counter() 호출만 기록하는 스파이 — 실제 prometheus 레지스트리를 만들지
    않는다(PLT-10 스펙의 "NullMetrics 스파이" 패턴과 동일)."""

    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, str] | None]] = []

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))


@pytest.fixture
def spy_metrics():
    spy = _SpyMetrics()
    set_metrics(spy)
    try:
        yield spy
    finally:
        set_metrics(NullMetrics())


def test_rebind_tenant_first_binding_without_previous_tenant_is_not_a_mismatch(spy_metrics):
    """negative: 인증 전에는 tenant_id가 아직 None이다(§3.1 계약) — 그 위에
    최초로 tenant_id를 얹는 것은 "불일치"가 아니므로 카운터를 올리면 안 된다."""
    trace_id = uuid.uuid4()
    ctx = TenantContext(tenant_id=uuid.uuid4(), subject_id=uuid.uuid4(), mfa_verified=True)

    with bind(trace_id=trace_id, tenant_id=None):
        rebind_tenant(ctx)
        assert observability_context.current().tenant_id == ctx.tenant_id
        assert observability_context.current().actor_subject_id == ctx.subject_id

    assert spy_metrics.counters == []


def test_rebind_tenant_same_tenant_id_repeated_does_not_count_as_mismatch(spy_metrics):
    """negative: 같은 tenant_id로 두 번 rebind해도(같은 요청 안에서 인증
    의존성이 여러 번 통과하는 정상 상황) 거짓양성 mismatch가 나면 안 된다."""
    trace_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    ctx = TenantContext(tenant_id=tenant_id, subject_id=uuid.uuid4(), mfa_verified=True)

    with bind(trace_id=trace_id, tenant_id=tenant_id):
        rebind_tenant(ctx)
        rebind_tenant(ctx)

    assert spy_metrics.counters == []


def test_rebind_tenant_mismatch_increments_metric_and_warns_but_stays_fail_open(
    spy_metrics, caplog
):
    """negative: 같은 trace_id 위에 서로 다른 tenant_id가 오면(비정상 상황,
    예: admin 교차 조회) INVARIANTS I1대로 fail-open — 예외를 던져 요청을
    막지 않되 mismatch 카운터를 올리고 경고 로그를 남긴다(스펙 446행)."""
    trace_id = uuid.uuid4()
    first_tenant = uuid.uuid4()
    second_tenant = uuid.uuid4()
    ctx = TenantContext(tenant_id=second_tenant, subject_id=uuid.uuid4(), mfa_verified=True)

    with caplog.at_level(logging.WARNING, logger="src.core.observability.tenant_binding"):
        with bind(trace_id=trace_id, tenant_id=first_tenant):
            rebind_tenant(ctx)  # fail-open: 예외 없이 진행
            assert observability_context.current().tenant_id == second_tenant

    assert spy_metrics.counters == [(AUTH_TENANT_MISMATCH_COUNT_TOTAL, None)]
    assert any(record.message == "tenant_mismatch" for record in caplog.records)


# ---------------------------------------------------------------------------
# task-3149 DEEPEN — RequestContextMiddleware.dispatch 실패 주입 + 성능 단언.
#
# 실DB/실앱 왕복(client 픽스처)은 라우팅·JSON 직렬화 시간이 섞여 미들웨어
# 자체 오버헤드를 격리 측정할 수 없다. dispatch()를 직접 호출해 call_next를
# 스텁으로 교체하면 trace_id 파싱·bind()·로그·메트릭만 남는다.
# ---------------------------------------------------------------------------


def _bare_request() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/perf-probe",
        "raw_path": b"/perf-probe",
        "query_string": b"",
        "headers": [],
        "app": SimpleNamespace(routes=[]),
        "client": ("test", 123),
        "server": ("test", 80),
        "scheme": "http",
    }
    return Request(scope)


async def _dispatch_p95_ms(middleware: RequestContextMiddleware, *, n: int) -> float:
    async def fast_call_next(request: Request) -> Response:
        return Response(status_code=200)

    durations_ms: list[float] = []
    for _ in range(n):
        request = _bare_request()
        start = time.perf_counter()
        await middleware.dispatch(request, fast_call_next)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_downstream_exception_still_emits_completion_log_before_propagating(spy_metrics):
    """실패 주입: call_next가 처리되지 않은 예외로 죽어도(다운스트림 핸들러
    버그) http_request_completed 로그 1줄은 finally 블록에서 먼저 기록된 뒤에
    예외가 그대로 전파된다 — 크래시가 감사 흔적을 지우지 않는다는 R1("모든
    요청이 끝까지 추적된다")의 fail-closed 성질을 직접 증명한다."""
    capture = _StructuredCapture()
    root = logging.getLogger()
    root.addHandler(capture)
    middleware = RequestContextMiddleware(app=lambda scope, receive, send: None)

    async def exploding_call_next(request: Request) -> Response:
        raise RuntimeError("boom - simulated handler crash")

    request = _bare_request()
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await middleware.dispatch(request, exploding_call_next)
    finally:
        root.removeHandler(capture)

    completed = [line for line in capture.lines if line.get("event") == "http_request_completed"]
    assert len(completed) == 1
    # call_next가 status_code를 절대 설정하지 못했으므로(예외로 죽음) dispatch()의
    # 방어적 초기값 500이 그대로 로그에 남는다.
    assert completed[0]["extra"]["status"] == 500


async def test_middleware_dispatch_overhead_p95_under_1ms_budget(spy_metrics):
    """수치 성능 단언: 108 §8 "미들웨어 오버헤드 p95 < 1 ms" 예산(스펙 524행).
    call_next를 즉시 반환하는 스텁으로 바꿔 실제 라우팅·DB 시간을 섞지 않고,
    dispatch() 자체의 오버헤드(trace_id 파싱·bind·로그·메트릭)만 200회 반복
    측정한다."""
    middleware = RequestContextMiddleware(app=lambda scope, receive, send: None)

    p95_ms = await _dispatch_p95_ms(middleware, n=200)

    assert p95_ms < 1.0


async def test_middleware_overhead_budget_gate_fails_on_injected_regression(
    spy_metrics, monkeypatch
):
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    dispatch()가 호출하는 logger.info에 2ms 인위 지연을 주입해, 같은 측정
    로직이 실제로 AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    middleware = RequestContextMiddleware(app=lambda scope, receive, send: None)
    original_info = request_context_module.logger.info

    def _slow_info(*args: object, **kwargs: object) -> None:
        time.sleep(0.002)
        original_info(*args, **kwargs)

    monkeypatch.setattr(request_context_module.logger, "info", _slow_info)

    p95_ms = await _dispatch_p95_ms(middleware, n=20)

    with pytest.raises(AssertionError):
        assert p95_ms < 1.0
