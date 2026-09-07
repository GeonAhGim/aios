"""RD-20 — OpenDART 공시 수집 유스케이스.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20,
ADR-2026-09-06-H D1/D2(source_contract 게이트 통과 전에는 어댑터를 부르지
않는다).

호출 순서는 항상: (1) `authorize_source_access`로 "OPENDART" 소스 접근이
허용되는지 확인 -> 거부면 미처리 큐에 남기고 끝, (2) 순수 함수
`normalize_filing`으로 정규화 -> 파싱 실패면 미처리 큐에 남기고 끝, (3)
`CorporateActionFilingRepository.append`로 저장(멱등, append-only). 어느
단계에서 실패해도 예외를 던져 배치 전체를 중단시키지 않는다 — 조용히
버리지도 않는다: 실패는 반드시 미처리 큐 행 하나로 남는다.
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
    """본문 텍스트를 담지 않는다 — `OpenDartFiling`이 애초에 구조화된
    필드만 갖고 있어(공시 원문 텍스트 없음) 별도 필터링이 필요 없다."""
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
