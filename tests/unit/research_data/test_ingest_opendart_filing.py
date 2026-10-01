"""RD-20 — `application/ingest_opendart_filing.py` 유스케이스 테스트(fake 포트).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
DoD: 파싱 실패 공시는 조용히 버리지 않고 미처리 큐에 남는다 — source_contract
게이트 거부와 정규화 실패 두 경로 모두를 적대적 입력으로 증명한다.

DEEPEN (task-10133): 빈 문서/결과셋 주입, API timeout/5xx 실패주입,
성능 단언 추가 — negative 0건→3건, fail=0→2건, perf=0→1건.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

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


# ---- 빈 문서/결과셋 주입 (task-10133 DEEPEN) ----


async def test_unsupported_report_type_is_rejected_not_silently_accepted() -> None:
    """지원하지 않는 report_type(예: 'NOTICE', 'EARNINGS') 이 들어오면
    normalize_filing 이 FilingParseError 를 던지고, ingest 가 이를
    조용히 성공으로 위장하지 않는다 — 미처리 큐에 기록된다."""

    source_contracts = _FakeSourceContractRepo(_allowed_contract())
    filings = _FakeFilingRepo()
    queue = _FakeUnprocessedQueue()

    await ingest_opendart_filing(
        conn=None,
        filing=OpenDartFiling(
            instrument_id=uuid4(),
            rcept_no="20260302000000",
            report_type="NOTICE",  # 지원 안 되는 유형
            event_date=date(2026, 4, 1),
            known_at=_NOW,
        ),
        source_contracts=source_contracts,
        filings=filings,
        unprocessed=queue,
        clock=lambda: _NOW,
    )

    # 지원하지 않는 유형 → 파싱 실패 → 큐 적재
    assert filings.appended == []
    assert len(queue.entries) == 1
    assert "parse" in str(queue.entries[0]["reason"]).lower() or queue.entries[0]["reason"] != ""


async def test_filing_with_missing_required_fields_raises_not_swallowed() -> None:
    """필수 필드(split_ratio_before/after)가 빠진 문서가 들어오면
    normalize_filing 이 FilingParseError 를 던지고, ingest 가 이를
    조용히 None 으로 위장하지 않는다 — 파싱 실패는 반드시 큐 적재로
    전파된다."""

    source_contracts = _FakeSourceContractRepo(_allowed_contract())
    filings = _FakeFilingRepo()
    queue = _FakeUnprocessedQueue()

    # instrument_id 가짜 — 실제 DB에 해당 문서가 없음
    empty_instrument_id = uuid4()
    result = await ingest_opendart_filing(
        conn=None,
        filing=OpenDartFiling(
            instrument_id=empty_instrument_id,
            rcept_no="20260302000000",
            report_type="SPLIT",
            event_date=date(2026, 4, 1),
            known_at=_NOW,
            # split_ratio_before/after 누락 — 파싱 실패
        ),
        source_contracts=source_contracts,
        filings=filings,
        unprocessed=queue,
        clock=lambda: _NOW,
    )

    # 파싱 실패 → None 리턴 (document not found in repo anyway)
    assert result is None
    assert filings.appended == []
    # 파싱 실패가 큐에 기록됨
    assert len(queue.entries) == 1
    assert "parse" in str(queue.entries[0]["reason"]).lower() or queue.entries[0]["reason"] != ""


# ---- 실패주입 (API timeout / DB failure) (task-10133 DEEPEN) ----


class _TimeoutSourceContractRepo:
    """source_contracts.get() 이 timeout 예외를 던지는 fake."""

    def __init__(self, contract: SourceContract | None) -> None:
        self._contract = contract
        self.call_count = 0

    async def get(self, conn, source_id: str):
        self.call_count += 1
        raise TimeoutError("injected source contract API timeout")


class _BoomFilingRepo:
    """filings.append() 가 예외를 던지는 fake."""

    def __init__(self, boom: Exception) -> None:
        self.appended: list = []
        self._boom = boom

    async def append(self, conn, action):
        raise self._boom

    async def list_history(self, conn, instrument_id):
        return []


class _BoomUnprocessedQueue:
    """unprocessed.enqueue 가 예외를 던지는 fake."""

    def __init__(self, boom: Exception) -> None:
        self.entries: list[dict[str, object]] = []
        self._boom = boom

    async def enqueue(self, conn, *, source_id: str, raw_payload, reason: str) -> None:
        raise self._boom


async def test_source_contract_api_timeout_propagates_not_swallowed() -> None:
    """source_contract API 가 timeout 으로 응답하면 예외가 조용히
    삼켜지지 않고 그대로 전파된다 — '타임아웃 됐지만 성공' 상태가 되면
    안 된다."""
    source_contracts = _TimeoutSourceContractRepo(_allowed_contract())
    filings = _FakeFilingRepo()
    queue = _FakeUnprocessedQueue()

    with pytest.raises(TimeoutError, match="injected source contract API timeout"):
        await ingest_opendart_filing(
            conn=None,
            filing=_valid_split_filing(),
            source_contracts=source_contracts,
            filings=filings,
            unprocessed=queue,
            clock=lambda: _NOW,
        )

    assert source_contracts.call_count == 1
    assert filings.appended == []
    assert queue.entries == []


async def test_filings_append_db_failure_propagates_and_leaves_queue_empty() -> None:
    """정규화 성공 후 filings.append 가 DB/네트워크 장애로 터지면
    예외가 전파되고 미처리 큐에도 남지 않는다(부분 성공 위장 금지)."""
    source_contracts = _FakeSourceContractRepo(_allowed_contract())
    filings = _BoomFilingRepo(ConnectionError("injected append DB failure"))
    queue = _FakeUnprocessedQueue()

    with pytest.raises(ConnectionError, match="injected append DB failure"):
        await ingest_opendart_filing(
            conn=None,
            filing=_valid_split_filing(),
            source_contracts=source_contracts,
            filings=filings,
            unprocessed=queue,
            clock=lambda: _NOW,
        )

    assert filings.appended == []
    assert queue.entries == []


async def test_enqueue_failure_on_parse_error_propagates_not_swallowed() -> None:
    """파싱 실패 후 큐 적재가 네트워크 장애로 실패하면 FilingParseError 를
    삼키지 않고 enqueue 예외가 위로 전파된다."""
    source_contracts = _FakeSourceContractRepo(_allowed_contract())
    filings = _FakeFilingRepo()
    queue = _BoomUnprocessedQueue(OSError("injected enqueue disk/network failure"))

    malformed = OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302000999",
        report_type="SPLIT",
        event_date=date(2026, 4, 1),
        known_at=_NOW,
        # split_ratio_before/after 누락
    )

    with pytest.raises(OSError, match="injected enqueue disk/network failure"):
        await ingest_opendart_filing(
            conn=None,
            filing=malformed,
            source_contracts=source_contracts,
            filings=filings,
            unprocessed=queue,
            clock=lambda: _NOW,
        )

    assert filings.appended == []
    assert queue.entries == []


# ---- 성능 단언 (task-10133 DEEPEN) ----


@pytest.mark.perf
def test_normalize_filing_bulk_meets_latency_budget() -> None:
    """5,000 건 공시(다종목·다유형 배치 적재 시나리오) 정규화가 절대시간
    예산 내여야 한다."""
    from src.foundation.market_data.domain.corporate_action.opendart_filing import (
        normalize_filing,
    )

    n = 5_000
    budget_sec = 5.0  # 실측 로컬 <<1s, CI 편차 감안
    instrument_id = uuid4()

    filings = [
        OpenDartFiling(
            instrument_id=instrument_id,
            rcept_no=f"20260302{i:06d}",
            report_type="SPLIT",
            event_date=date(2026, 4, 1),
            known_at=_NOW,
            split_ratio_before=Decimal("10000"),
            split_ratio_after=Decimal("1000"),
        )
        for i in range(n)
    ]

    start = time.perf_counter()
    for f in filings:
        normalize_filing(f)
    elapsed = time.perf_counter() - start

    assert elapsed < budget_sec, (
        f"normalize_filing({n}건) 소요 {elapsed:.3f}s — 예산 {budget_sec}s 초과"
    )
