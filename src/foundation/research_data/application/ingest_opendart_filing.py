"""RD-20 — OpenDART filing ingestion use case.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20,
ADR-2026-09-06-H D1/D2 (the adapter must not be called before the
source_contract gate is passed).

The call order is always: (1) confirm via `authorize_source_access` that
access to the "OPENDART" source is allowed -> if denied, leave it in the
unprocessed queue and stop; (2) normalize with the pure function
`normalize_filing` -> if parsing fails, leave it in the unprocessed queue
and stop; (3) persist via `CorporateActionFilingRepository.append`
(idempotent, append-only). A failure at any stage must not throw an
exception that aborts the whole batch — nor should it be silently
dropped: every failure must leave exactly one row in the unprocessed
queue.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

import asyncpg

from src.foundation.market_data.application.authorize_source_access import (
    authorize_source_access,
)
from src.foundation.market_data.contracts.v1 import CorporateAction
from src.foundation.market_data.domain.corporate_action.opendart_filing import (
    FilingParseError,
    OpenDartFiling,
    normalize_filing,
)
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository
from src.foundation.research_data.ports.corporate_action_filing_repository import (
    CorporateActionFilingRepository,
)
from src.foundation.research_data.ports.unprocessed_filing_queue import UnprocessedFilingQueue

__all__ = ["OPENDART_SOURCE_ID", "ingest_opendart_filing"]

OPENDART_SOURCE_ID = "OPENDART"


def _raw_payload(filing: OpenDartFiling) -> dict[str, object]:
    """Does not carry the filing body text — `OpenDartFiling` only ever holds
    structured fields (no raw filing text), so no separate filtering is
    needed."""
    payload = asdict(filing)
    payload["instrument_id"] = str(filing.instrument_id)
    payload["event_date"] = filing.event_date.isoformat()
    payload["known_at"] = filing.known_at.isoformat()
    for field in ("split_ratio_before", "split_ratio_after", "dividend_per_share", "merger_ratio"):
        if payload[field] is not None:
            payload[field] = str(payload[field])
    return payload


async def ingest_opendart_filing(
    conn: asyncpg.Connection,
    filing: OpenDartFiling,
    *,
    source_contracts: SourceContractRepository,
    filings: CorporateActionFilingRepository,
    unprocessed: UnprocessedFilingQueue,
    clock: Callable[[], datetime],
) -> CorporateAction | None:
    grant = await authorize_source_access(
        conn, OPENDART_SOURCE_ID, repo=source_contracts, clock=clock
    )
    if not grant.allowed:
        await unprocessed.enqueue(
            conn,
            source_id=OPENDART_SOURCE_ID,
            raw_payload=_raw_payload(filing),
            reason=f"source_contract_denied:{grant.denial_reason}",
        )
        return None

    try:
        action = normalize_filing(filing)
    except FilingParseError as exc:
        await unprocessed.enqueue(
            conn,
            source_id=OPENDART_SOURCE_ID,
            raw_payload=_raw_payload(filing),
            reason=str(exc),
        )
        return None

    return await filings.append(conn, action)
