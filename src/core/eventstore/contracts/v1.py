"""FA-13 — Event store contract v1.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§2.4 FA-13,
107_contract_versioning_and_compatibility_standard_v1.0.md.

`DomainEvent` is the sole public surface of `core/eventstore/` —
`append.py`, `replay.py` (FA-15), and `projections/*.py` (FA-14) exchange
events exclusively through this contract. Adding fields is a minor change
(107, defaults required); removing fields or changing semantics requires a
new `v2` module.

`hash` / `prev_hash` are not values computed by the caller — they are
hash-chain links assigned by the adapter (`append.py`) via a
`(stream_id, seq)` UNIQUE constraint + conditional INSERT (§5).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel

SCHEMA_VERSION: Literal["v1"] = "v1"


class DomainEvent(BaseModel):
    """View of a single append-only `event_store` row (§2.4 table)."""

    stream_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    causation_id: str | None
    correlation_id: str | None
    hash: str
    prev_hash: str | None
    schema_version: Literal["v1"] = SCHEMA_VERSION
