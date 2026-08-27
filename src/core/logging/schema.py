"""7.1 — LogEntry 스키마 + 로거 초기화.

Spec: 07_logging_config_v1.3.md#§7.1

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
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


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
    형태로 호출하면 각각 LogEntry.event_type/correlation_id/extra로 매핑된다."""

    def format(self, record: logging.LogRecord) -> str:
        entry = LogEntry(
            timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc),
            level=record.levelname,
            module=record.name,
            event_type=getattr(record, "event_type", "log.unstructured"),
            correlation_id=getattr(record, "correlation_id", None),
            message=record.getMessage(),
            extra=getattr(record, "payload", {}),
        )
        return entry.model_dump_json()


def configure_logging(level: str = "INFO") -> None:
    """애플리케이션 시작 시 1회 호출 — 루트 로거에 JSON Lines stdout 핸들러를 부착한다."""
    handler = logging.StreamHandler()
    handler.setFormatter(JSONLinesFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
