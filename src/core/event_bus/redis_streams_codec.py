"""4.7 (M2-8 Phase2) — EventEnvelope <-> Redis Stream wire encoding.

Split out of `redis_streams.py` to stay under the architecture guard's
300-line-per-file cap (policy/immutable.md P6) — this module owns exactly
one concern: turning an `EventEnvelope` into the string stored in a Redis
Stream entry and back.

`EventEnvelope.payload` is `Any` — there is no schema to persist against.
Only `Decimal`/`UUID`/`datetime` are converted to strings for the wire; any
other JSON-non-serializable value (a `set`, a custom object, ...) makes
serialization raise immediately (fail-closed) instead of silently mangling
e.g. a monetary amount via `str(obj)`.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.core.event_bus.envelope import EventEnvelope


def _json_default(obj: Any) -> str:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(
        "RedisStreamsEventBus: payload contains a value that is not JSON "
        "serializable — the persistent backend requires payloads built only "
        f"from dict/list/str/int/float/bool/None/Decimal/UUID/datetime: {type(obj)!r}"
    )


def serialize_envelope(envelope: EventEnvelope) -> str:
    data = {
        "event_id": str(envelope.event_id),
        "topic": envelope.topic,
        "trace_id": str(envelope.trace_id),
        "tenant_id": str(envelope.tenant_id) if envelope.tenant_id is not None else None,
        "actor_subject_id": (
            "system" if envelope.actor_subject_id == "system" else str(envelope.actor_subject_id)
        ),
        "occurred_at": envelope.occurred_at.isoformat(),
        "schema_version": envelope.schema_version,
        "payload": envelope.payload,
    }
    return json.dumps(data, default=_json_default)


def deserialize_envelope(raw: str) -> EventEnvelope:
    data = json.loads(raw)
    return EventEnvelope(
        event_id=UUID(data["event_id"]),
        topic=data["topic"],
        trace_id=UUID(data["trace_id"]),
        tenant_id=UUID(data["tenant_id"]) if data.get("tenant_id") else None,
        actor_subject_id=(
            "system" if data["actor_subject_id"] == "system" else UUID(data["actor_subject_id"])
        ),
        occurred_at=datetime.fromisoformat(data["occurred_at"]),
        schema_version=data.get("schema_version", "v1"),
        payload=data["payload"],
    )
