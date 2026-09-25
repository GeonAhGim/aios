import json
import logging
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import QueueHandler

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from src.core.logging import fields as log_fields
from src.core.logging.fields import REQUIRED_FIELDS
from src.core.logging.redaction import REDACTED, RedactionFilter
from src.core.logging.request_context import request_id_var
from src.core.logging.schema import JSONLinesFormatter, LogEntry, configure_logging
from src.core.observability.context import bind


def _make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="src.exchanges.bitget.adapter",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="주문 재시도",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_produces_valid_json_lines_matching_log_entry():
    record = _make_record(event_type="order.status.changed", correlation_id="c-1", payload={"n": 1})
    formatted = JSONLinesFormatter().format(record)

    data = json.loads(formatted)
    entry = LogEntry(**data)
    assert entry.level == "WARNING"
    assert entry.module == "src.exchanges.bitget.adapter"
    assert entry.event_type == "order.status.changed"
    assert entry.correlation_id == "c-1"
    assert entry.extra == {"n": 1}
    assert entry.message == "주문 재시도"


def test_formatter_defaults_when_extra_fields_absent():
    record = _make_record()
    entry = LogEntry(**json.loads(JSONLinesFormatter().format(record)))

    assert entry.event_type == "log.unstructured"
    assert entry.correlation_id is None
    assert entry.extra == {}


def test_formatter_falls_back_to_request_id_contextvar_when_correlation_id_absent():
    """request_id 미들웨어(src/api/middleware/request_id.py)가 이 값을
    설정해두면, 호출자가 correlation_id를 명시하지 않은 로그도 자동으로
    요청 ID가 찍혀야 한다."""
    token = request_id_var.set("req-abc123")
    try:
        record = _make_record()
        entry = LogEntry(**json.loads(JSONLinesFormatter().format(record)))
    finally:
        request_id_var.reset(token)

    assert entry.correlation_id == "req-abc123"


def test_formatter_explicit_correlation_id_wins_over_request_id_contextvar():
    """AIOSTask.task_id처럼 호출자가 의도적으로 지정한 correlation_id는
    요청 ID로 덮어써지면 안 된다."""
    token = request_id_var.set("req-abc123")
    try:
        record = _make_record(correlation_id="task-xyz")
        entry = LogEntry(**json.loads(JSONLinesFormatter().format(record)))
    finally:
        request_id_var.reset(token)

    assert entry.correlation_id == "task-xyz"


def test_configure_logging_attaches_json_formatter_to_root():
    listener = configure_logging(level="DEBUG")
    try:
        root = logging.getLogger()

        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JSONLinesFormatter)
        assert root.level == logging.DEBUG
    finally:
        # QueueListener는 데몬 스레드를 띄운다 — 명시적으로 stop()하지 않으면
        # 다음 테스트로 스레드가 새어 나가 flaky의 원인이 된다.
        listener.stop()


def test_configure_logging_attaches_queue_handler_for_non_blocking_emit():
    """실제 stdout 쓰기는 QueueListener 스레드가 하고, 호출 스레드는 큐에 넣기만
    한다 — root에 붙는 건 QueueHandler여야 한다(§9 PLT-03 "로그 sink 지연/stdout
    막힘" 리스크 대응)."""
    listener = configure_logging(level="INFO")
    try:
        root = logging.getLogger()
        assert isinstance(root.handlers[0], QueueHandler)
    finally:
        listener.stop()


def test_configure_logging_end_to_end_emits_redacted_8_field_json_line(capsys):
    listener = configure_logging(level="INFO")
    try:
        with bind(tenant_id=uuid.uuid4()) as ctx:
            logging.getLogger("src.exchanges.bitget.adapter").warning(
                "주문 재시도",
                extra={
                    "event_type": "order.status.changed",
                    "payload": {"api_key": "abcd1234", "n": 1},
                },
            )
    finally:
        # stop()은 sentinel을 넣고 리스너 스레드를 join하므로, 이미 큐에 있던
        # 레코드가 먼저 처리된 뒤에 반환된다 — sleep 없이 결정적으로 flush된다.
        listener.stop()

    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])

    for field_name in REQUIRED_FIELDS:
        assert field_name in line
    assert line["level"] == "WARNING"  # 07 §7.1 계약 유지 — 108의 소문자 매핑 아님
    assert line["extra"]["api_key"] == REDACTED
    assert line["extra"]["n"] == 1
    # 리스너 스레드에서 다시 계산한 fallback 값이 아니라, 로그 호출 시점(호출
    # 스레드)에 실제로 바인딩돼 있던 RequestContext 값이어야 한다.
    assert line["trace_id"] == str(ctx.trace_id)
    assert line["tenant_id"] == str(ctx.tenant_id)
    # QueueHandler.prepare()가 큐에 넣기 전에 미리 포맷해버리면 리스너 스레드가
    # 그 결과를 다시 감싸 "message"가 JSON 블롭이 된다(이중 인코딩) — 원본 메시지
    # 그대로여야 한다.
    assert line["message"] == "주문 재시도"


def test_configure_logging_queue_handler_does_not_double_encode_message(capsys):
    """`_ContextCapturingQueueHandler.prepare()`는 컨텍스트 스냅샷만 얹고 문자열로
    굳히지 않는다 — 굳혀버리면(QueueHandler 기본 동작) 리스너 스레드가 이미 JSON인
    문자열을 다시 JSONLinesFormatter에 통과시켜 "message" 필드가 통째로 중첩 JSON이
    된다."""
    listener = configure_logging(level="INFO")
    try:
        logging.getLogger("x").info("plain message", extra={"event_type": "t"})
    finally:
        listener.stop()

    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])

    assert line["message"] == "plain message"
    # 이중 인코딩됐다면 message 자체가 `{`로 시작하는 JSON 문자열이 된다.
    assert not line["message"].startswith("{")


def test_formatter_json_line_carries_all_108_required_fields():
    with bind(tenant_id=uuid.uuid4()):
        record = _make_record(
            event_type="order.status.changed", correlation_id="c-1", payload={"n": 1}
        )
        entry = json.loads(JSONLinesFormatter().format(record))

    for field_name in REQUIRED_FIELDS:
        assert field_name in entry
    # 기존 07 §7.1 필드도 함께 실려야 한다(추가만 허용, 삭제/치환 금지).
    assert entry["module"] == "src.exchanges.bitget.adapter"
    assert entry["event_type"] == "order.status.changed"


def test_formatter_preserves_legacy_level_value_over_108_lowercase_mapping():
    """108 §2의 `level`은 소문자(warn/error 등)로 매핑되지만, 07 §7.1 소비처는
    `record.levelname` 원문(WARNING 등)을 기대한다 — 필드 이름이 겹치는
    유일한 경우이므로 기존 계약이 이긴다."""
    record = _make_record()
    entry = json.loads(JSONLinesFormatter().format(record))

    assert entry["level"] == "WARNING"


def test_formatter_extra_reflects_redaction_filter_applied_before_format():
    record = _make_record(payload={"api_key": "abcd1234", "note": "ok"})
    RedactionFilter().filter(record)

    entry = json.loads(JSONLinesFormatter().format(record))

    assert entry["extra"]["api_key"] == REDACTED
    assert entry["extra"]["note"] == "ok"


# ── negative tests (invariant-violating 입력 명시적 거부) ───────────────────


def test_log_entry_rejects_missing_required_field():
    """음성 테스트: `message`는 LogEntry의 필수 필드다 — 누락된 채로 생성을
    시도하면 조용히 기본값을 채우는 대신 ValidationError로 거부해야 한다
    (07 §7.1 계약 필드 누락을 fail-closed로 처리)."""
    with pytest.raises(ValidationError):
        LogEntry(
            timestamp=datetime.now(timezone.utc),
            level="WARNING",
            module="m",
            event_type="e",
        )


def test_log_entry_rejects_non_dict_extra():
    """음성 테스트: `extra`는 dict[str, Any] 계약이다 — list 등 다른 타입을
    넘기면 어떤 형태로든 강제 변환하지 않고 ValidationError로 거부해야
    한다."""
    with pytest.raises(ValidationError):
        LogEntry(
            timestamp=datetime.now(timezone.utc),
            level="WARNING",
            module="m",
            event_type="e",
            message="hi",
            extra=["not", "a", "dict"],
        )


def test_formatter_raises_on_non_json_serializable_payload():
    """음성 테스트: payload(extra)에 JSON으로 직렬화할 수 없는 객체가 실리면
    `JSONLinesFormatter.format()`은 조용히 문자열로 치환하지 않고
    PydanticSerializationError를 던져야 한다 — 로그 라인이 손상된 채 나가는
    것보다 fail-closed가 낫다."""
    record = _make_record(payload={"bad": object()})

    with pytest.raises(PydanticSerializationError):
        JSONLinesFormatter().format(record)


# ── 실패주입 (monkeypatch 의존성 예외 유발) ─────────────────────────────────


def test_formatter_fails_closed_when_required_field_unknown_to_structured_log_line(
    monkeypatch: pytest.MonkeyPatch,
):
    """실패주입: `fields.REQUIRED_FIELDS`(단일 출처, PLT-02)에 `StructuredLogLine`이
    만들어내지 못하는 필드 이름이 섞여 들어오면, `JSONLinesFormatter.format()`은
    그 필드를 조용히 건너뛰지 않고 KeyError로 죽어야 한다 — 108 §2 필수 필드
    목록과 실제 값 생성 로직이 어긋난 상태로 로그가 나가는 것을 막는다."""
    monkeypatch.setattr(log_fields, "REQUIRED_FIELDS", (*log_fields.REQUIRED_FIELDS, "bogus_field"))
    record = _make_record()

    with pytest.raises(KeyError):
        JSONLinesFormatter().format(record)


# ── 성능 단언 + 게이트 적색 재현 ─────────────────────────────────────────────


def _format_latencies_ms(iterations: int = 200) -> list[float]:
    latencies: list[float] = []
    for i in range(iterations):
        record = _make_record(
            event_type="order.status.changed",
            correlation_id=f"c-{i}",
            payload={"n": i},
        )
        start = time.perf_counter()
        JSONLinesFormatter().format(record)
        latencies.append((time.perf_counter() - start) * 1000)
    return latencies


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, int(len(ordered) * 0.95) - 1)
    return ordered[index]


# §9 PLT-03 리스크표의 "로그 sink 지연/stdout 막힘" 대응은 QueueHandler의
# 비동기 큐잉 경로에 의존한다 — 그 경로가 실제로 non-blocking이려면
# 리스너 스레드에서 도는 `format()` 자체도 예산 안에서 끝나야 큐가 밀리지
# 않는다. ADR-2026-09-09-C Decision 1 축별 예산표에 로깅 전용 항목은 없어
# (순수 인메모리 JSON 직렬화, I/O 없음), 로컬 실측(수백 마이크로초) 대비
# 10배 이상 여유를 둔 예산을 이 리프가 자체 선언한다.
_FORMAT_P95_BUDGET_MS = 5.0


def test_formatter_format_latency_p95_within_self_declared_budget():
    """성능 단언: `JSONLinesFormatter.format()`의 p95 지연이 자체 선언 예산
    (5ms) 안에 있어야 한다 — 이 경로가 느려지면 QueueListener 스레드가 밀려
    §9 PLT-03이 막으려는 stdout 블로킹이 큐 뒤에서 재발한다."""
    p95_ms = _p95(_format_latencies_ms())
    assert p95_ms < _FORMAT_P95_BUDGET_MS


def test_gate_red_repro_format_latency_budget_actually_fails_on_regression(
    monkeypatch: pytest.MonkeyPatch,
):
    """게이트 적색 재현: 위 성능 단언이 상시-녹색 tautology가 아니라는 것을
    증명한다 — `json.dumps`에 예산을 넘는 지연을 주입하면 같은 단언식이
    실제로 AssertionError를 내야 한다."""
    import src.core.logging.schema as schema_module

    original_dumps = schema_module.json.dumps

    def _stalled_dumps(*args, **kwargs):
        time.sleep(_FORMAT_P95_BUDGET_MS / 1000.0)
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(schema_module.json, "dumps", _stalled_dumps)

    p95_ms = _p95(_format_latencies_ms(iterations=5))
    with pytest.raises(AssertionError):
        assert p95_ms < _FORMAT_P95_BUDGET_MS
