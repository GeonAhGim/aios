"""RD-20 — `application/ingest_opendart_filing.py` 유스케이스 테스트(fake 포트).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
DoD: 파싱 실패 공시는 조용히 버리지 않고 미처리 큐에 남는다 — source_contract
게이트 거부와 정규화 실패 두 경로 모두를 적대적 입력으로 증명한다.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.foundation.market_data.domain.corporate_action.opendart_filing import OpenDartFiling
from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
)
from src.foundation.research_data.application.ingest_opendart_filing import (
    OPENDART_SOURCE_ID,
    ingest_opendart_filing,
)

_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


class _FakeSourceContractRepo:
    def __init__(self, contract: SourceContract | None) -> None:
        self._contract = contract

    async def get(self, conn, source_id: str):
        return self._contract if source_id == OPENDART_SOURCE_ID else None


class _FakeFilingRepo:
    def __init__(self) -> None:
        self.appended: list = []

    async def append(self, conn, action):
        self.appended.append(action)
        return action

    async def list_history(self, conn, instrument_id):
        return [a for a in self.appended if a.instrument_id == instrument_id]


class _FakeUnprocessedQueue:
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []

    async def enqueue(self, conn, *, source_id: str, raw_payload, reason: str) -> None:
        self.entries.append({"source_id": source_id, "raw_payload": raw_payload, "reason": reason})


def _allowed_contract() -> SourceContract:
    return SourceContract(
        source_id=OPENDART_SOURCE_ID,
        tier=SourceContractTier.FREE,
        credential_ref="vault:opendart:v1",
        redistribution_scope=RedistributionScope.DISPLAY,
        rate_limit=1000,
        quota=10000,
        valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        valid_to=None,
        capability=SourceCapability(
            asset_classes=frozenset({"EQUITY_KR"}),
            resolutions=frozenset(),
            corporate_actions=True,
        ),
    )


def _valid_split_filing() -> OpenDartFiling:
    return OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302000123",
        report_type="SPLIT",
        event_date=date(2026, 4, 1),
        known_at=_NOW,
        split_ratio_before=Decimal("5000"),
        split_ratio_after=Decimal("500"),
    )


async def test_source_contract_denied_never_calls_adapter_and_lands_in_queue() -> None:
    source_contracts = _FakeSourceContractRepo(None)  # 미등록 -> fail-closed 거부
    filings = _FakeFilingRepo()
    queue = _FakeUnprocessedQueue()

    result = await ingest_opendart_filing(
        conn=None,
        filing=_valid_split_filing(),
        source_contracts=source_contracts,
        filings=filings,
        unprocessed=queue,
        clock=lambda: _NOW,
    )

    assert result is None
    assert filings.appended == []
    assert len(queue.entries) == 1
    assert queue.entries[0]["source_id"] == OPENDART_SOURCE_ID
    assert "source_contract_denied" in str(queue.entries[0]["reason"])


async def test_parse_failure_is_not_silently_dropped_and_lands_in_queue() -> None:
    source_contracts = _FakeSourceContractRepo(_allowed_contract())
    filings = _FakeFilingRepo()
    queue = _FakeUnprocessedQueue()
    malformed = OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302000999",
        report_type="SPLIT",
        event_date=date(2026, 4, 1),
        known_at=_NOW,
        # split_ratio_before/after 누락 — 파싱 실패
    )

    result = await ingest_opendart_filing(
        conn=None,
        filing=malformed,
        source_contracts=source_contracts,
        filings=filings,
        unprocessed=queue,
        clock=lambda: _NOW,
    )

    assert result is None
    assert filings.appended == []
    assert len(queue.entries) == 1
    assert queue.entries[0]["reason"] != ""
    assert "본문" not in str(queue.entries[0]["raw_payload"])


async def test_valid_filing_with_allowed_contract_is_appended() -> None:
    source_contracts = _FakeSourceContractRepo(_allowed_contract())
    filings = _FakeFilingRepo()
    queue = _FakeUnprocessedQueue()

    result = await ingest_opendart_filing(
        conn=None,
        filing=_valid_split_filing(),
        source_contracts=source_contracts,
        filings=filings,
        unprocessed=queue,
        clock=lambda: _NOW,
    )

    assert result is not None
    assert result.ratio == Decimal("10")
    assert filings.appended == [result]
    assert queue.entries == []
