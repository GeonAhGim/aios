"""7.1 — LogEntry 스키마 + 로거 초기화.

Spec: 07_logging_config_v1.3.md#§7.1,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-03.

Phase 1은 stdout으로 JSON Lines 출력 — 로그 수집기(Datadog/Loki 등)는 팀
확정 후 연결한다(과잉설계 방지, 17.9-A).

로그 레벨 사용 기준:
DEBUG    — 개발 중에만. 프로덕션 기본 비활성.
INFO     — 정상 주문 생성/체결, 정상 상태 전이.
WARNING  — 재시도 발생, Reconciliation 1회 불일치(8.4), Circuit Breaker 경고.
ERROR    — Handler 예외(EventHandlerError), 주문 거부, API 인증 실패.
CRITICAL — Watchdog 발동, Circuit Breaker 거래중지 이상, Kill Switch 발동.
           이 레벨은 반드시 audit_log 테이블에도 동시 기록(8.10 원칙) —
           실제 연결은 7.4(audit_log 기록 유틸)가 준비된 후 애플리케이션
           조립 단계(main.py)에서 CRITICAL 핸들러로 배선한다.

PLT-03: `LogEntry`(07 §7.1 계약, 기존 소비처 존재)는 필드 이름·값 계약을 그대로
유지하고, 108 §2 8필드는 `fields.py`(단일 출처, PLT-02)에서 위임받아 JSON 출력에
추가한다. 필드 목록은 여기서 다시 하드코딩하지 않고 `fields.REQUIRED_FIELDS`를
순회해 `LogEntry`에 없는 키만 채운다 — 유일한 예외는 `level`이다. `LogEntry`가
이미 동일한 이름의 필드를 갖고 있고(`record.levelname` 원문, 예: "WARNING") 108
쪽은 소문자 매핑값("warn")을 쓰므로, 기존 소비처의 값 계약을 깨지 않기 위해
108의 `level`로 덮어쓰지 않는다.
"""
from __future__ import annotations

import json
import logging
import queue
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener
from typing import Any

from pydantic import BaseModel, Field

from src.core.logging import fields as log_fields
from src.core.logging.redaction import RedactionFilter
from src.core.logging.request_context import get_current_request_id
from src.core.observability.context import current as current_request_context


class LogEntry(BaseModel):
    timestamp: datetime
    level: str
    module: str
    event_type: str  # 05번 문서 Topic 명명규칙과 동일 체계(예: "order.status.changed")
    correlation_id: str | None = None  # AIOSTask.task_id 또는 Order.client_order_id
    message: str
    extra: dict[str, Any] = Field(default_factory=dict)


class JSONLinesFormatter(logging.Formatter):
    """`logger.info(msg, extra={"event_type": ..., "correlation_id": ..., "payload": {...}})`
    형태로 호출하면 각각 LogEntry.event_type/correlation_id/extra로 매핑된다.

    출력 JSON 라인은 `LogEntry` 7필드에 더해 108 §2 8필드(`fields.REQUIRED_FIELDS`)를
    싣는다 — `level`을 제외한 7개는 `fields.from_record()`가 현재 `RequestContext`로
    계산한 값이고, `level`은 위 docstring 이유로 `LogEntry`가 채운 값을 유지한다.
    """

    def format(self, record: logging.LogRecord) -> str:
        # 호출자가 correlation_id를 명시하지 않았으면 요청 미들웨어가 채워둔
        # request_id로 대체한다(HTTP 요청 컨텍스트 밖이면 여전히 None).
        correlation_id = getattr(record, "correlation_id", None) or get_current_request_id()
        entry = LogEntry(
            timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc),
            level=record.levelname,
            module=record.name,
            event_type=getattr(record, "event_type", "log.unstructured"),
            correlation_id=correlation_id,
            message=record.getMessage(),
            extra=getattr(record, "payload", {}),
        )
        line = entry.model_dump(mode="json")

        structured = log_fields.from_record(record, current_request_context())
        structured_line = structured.model_dump(mode="json")
        for field_name in log_fields.REQUIRED_FIELDS:
            line.setdefault(field_name, structured_line[field_name])

        return json.dumps(line, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, redact: bool = True) -> QueueListener:
    """애플리케이션 시작 시 1회 호출 — 루트 로거에 비차단 `QueueHandler`를 부착한다.

    실제 stdout 쓰기(포맷팅 + 선택적 레닥션)는 `QueueListener`가 별도 스레드에서
    수행한다 — 호출 스레드(주문 실행 경로 포함)는 큐에 넣기만 하고 즉시 반환하므로
    로그 sink 지연/stdout 막힘이 거래 경로를 블로킹하지 않는다(§9 PLT-03 리스크
    대응표: "로그 sink 지연/stdout 막힘" → "QueueHandler로 로깅 비동기화"). 반환된
    `QueueListener`는 호출자가 `stop()`으로 명시적으로 멈춰야 한다 — 특히 테스트에서
    멈추지 않으면 리스너 스레드가 다음 테스트로 새어 나가 flaky의 원인이 된다.
    """
    formatter = JSONLinesFormatter()

    target_handler = logging.StreamHandler()
    target_handler.setFormatter(formatter)
    if redact:
        target_handler.addFilter(RedactionFilter())

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    listener = QueueListener(log_queue, target_handler, respect_handler_level=True)
    listener.start()

    queue_handler = QueueHandler(log_queue)
    # QueueHandler.emit()은 format()을 호출하지 않는다(레코드를 그대로 큐에 넣기만
    # 한다) — 실제 포맷팅은 위 target_handler가 리스너 스레드에서 수행한다. 그래도
    # formatter를 여기 붙여두는 건 하위호환 때문이다: 이 핸들러가 부착되기 전에도
    # `root.handlers[0].formatter`로 JSONLinesFormatter 존재를 확인하던 소비처가 있다.
    queue_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(queue_handler)
    root.setLevel(level)
    return listener
