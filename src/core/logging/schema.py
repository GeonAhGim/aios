"""7.1 — LogEntry schema + logger initialization.

Spec: 07_logging_config_v1.3.md#§7.1,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-03.

Phase 1 outputs JSON Lines to stdout — log collectors (Datadog, Loki, etc.)
will be connected after the team finalizes them (prevents over-engineering,
17.9-A).

Log level usage criteria:
DEBUG    — Development only. Disabled by default in production.
INFO     — Normal order creation/filling, normal state transitions.
WARNING  — Retry triggered, 1 Reconciliation mismatch (8.4), Circuit Breaker alert.
ERROR    — Handler exception (EventHandlerError), order rejection, API auth failure.
CRITICAL — Watchdog triggered, Circuit Breaker halt above threshold, Kill Switch activated.
           This level must also be recorded simultaneously in the audit_log table (8.10 principle) —
           actual wiring to the CRITICAL handler occurs in the application
           assembly stage (main.py) after 7.4 (audit_log recording utility) is ready.

PLT-03: `LogEntry` (07 §7.1 contract, existing consumers exist) preserves its
field name/value contract as-is, and the 8 fields in 108 §2 are delegated
from `fields.py` (single source of truth, PLT-02) and appended to JSON output.
The field list is not hardcoded here; instead we iterate `fields.REQUIRED_FIELDS`
and fill only keys not already present in `LogEntry` — the sole exception is
`level`. `LogEntry` already has a field with the same name
(`record.levelname` original, e.g. "WARNING"), while 108 uses the lowercase
mapped value ("warn"), so we do not overwrite with 108's `level` to avoid
breaking existing consumers' value contract.
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
    event_type: str  # Same naming scheme as doc 05 Topic conventions (e.g. "order.status.changed")
    correlation_id: str | None = None  # AIOSTask.task_id or Order.client_order_id
    message: str
    extra: dict[str, Any] = Field(default_factory=dict)


class JSONLinesFormatter(logging.Formatter):
    """When called as:
    `logger.info(msg, extra={"event_type": ..., "correlation_id": ..., "payload": {...}})`,
    each extra key maps to LogEntry.event_type/correlation_id/extra respectively.

    The output JSON line carries `LogEntry`'s 7 fields plus 8 fields from 108 §2
    (`fields.REQUIRED_FIELDS`) — the 7 fields excluding `level` are computed by
    `fields.from_record()` from the current `RequestContext`, and `level` preserves
    the value filled by `LogEntry` for the reason stated above in this docstring.
    """

    def format(self, record: logging.LogRecord) -> str:
        # If the caller did not specify correlation_id, fall back to the
        # request_id populated by request middleware (still None outside HTTP request context).
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

        # On the QueueHandler path, `_ContextCapturingQueueHandler.prepare()`
        # pre-takes a snapshot on the original thread (= the thread where RequestContext
        # was bound) and attaches it — the QueueListener thread running this
        # format() does not propagate ContextVars, so calling `current_request_context()`
        # here would always return the fallback (default) value. Direct calls that
        # bypass QueueHandler (tests, etc.) have no snapshot, so we compute it
        # on this thread directly.
        ctx = getattr(record, "structured_context", None) or current_request_context()
        structured = log_fields.from_record(record, ctx)
        structured_line = structured.model_dump(mode="json")
        for field_name in log_fields.REQUIRED_FIELDS:
            line.setdefault(field_name, structured_line[field_name])

        return json.dumps(line, ensure_ascii=False)


class _ContextCapturingQueueHandler(QueueHandler):
    """The default `QueueHandler.prepare()` implementation overwrites the
    record with a fully formatted string via `self.format()` before queueing —
    if the listener thread calls `format()` again with the same formatter,
    it double-encodes an already-JSON string. So we do not materialize as a
    string here, but instead only snapshot the current context from the calling
    thread (= the thread where RequestContext is actually bound) onto the record —
    actual JSON rendering is performed exactly once by the listener thread's
    `target_handler`, as per the original design."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        record.structured_context = current_request_context()
        return record


def configure_logging(level: str = "INFO", *, redact: bool = True) -> QueueListener:
    """Call once at application startup — attaches a non-blocking `QueueHandler`
    to the root logger.

    Actual stdout writing (formatting + optional redaction) is performed by
    `QueueListener` on a separate thread — the calling thread (including order
    execution paths) only queues the record and returns immediately, so log
    sink latency/stdout blocking does not block the trading path (§9 PLT-03
    risk mitigation table: "log sink latency/stdout blocking" → "async logging
    via QueueHandler"). The returned `QueueListener` must be explicitly stopped
    by the caller via `stop()` — especially in tests, if not stopped the
    listener thread leaks into the next test, causing flakiness.
    """
    formatter = JSONLinesFormatter()

    target_handler = logging.StreamHandler()
    target_handler.setFormatter(formatter)
    if redact:
        target_handler.addFilter(RedactionFilter())

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    listener = QueueListener(log_queue, target_handler, respect_handler_level=True)
    listener.start()

    queue_handler = _ContextCapturingQueueHandler(log_queue)
    # emit() itself still only queues — actual formatting is done by the above
    # target_handler on the listener thread (prepare() only attaches a context
    # snapshot, see the class docstring above). We keep the formatter here for
    # backward compatibility: there are consumers that check for JSONLinesFormatter
    # existence via `root.handlers[0].formatter` even before this handler is attached.
    queue_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(queue_handler)
    root.setLevel(level)
    return listener
