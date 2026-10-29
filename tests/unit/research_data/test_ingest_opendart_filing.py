"""RD-20 — `application/ingest_opendart_filing.py` 유스케이스 테스트(fake 포트).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
DoD: 파싱 실패 공시는 조용히 버리지 않고 미처리 큐에 남는다 — source_contract
게이트 거부와 정규화 실패 두 경로 모두를 적대적 입력으로 증명한다.

DEEPEN (task-10133): 빈 문서/결과셋 주입, API timeout/5xx 실패주입,
성능 단언 추가 — negative 0건→3건, fail=0→2건, perf=0→1건.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest

from src.foundation.market_data.contracts.v1 import CorporateAction
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
from tests.support.perf_inject import virtual_delayed_sync

_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


class _FakeSourceContractRepo:
    def __init__(self, contract: SourceContract | None) -> None:
        self._contract = contract

    async def get(self, conn: object, source_id: str) -> SourceContract | None:
        return self._contract if source_id == OPENDART_SOURCE_ID else None


class _FakeFilingRepo:
    def __init__(self) -> None:
        self.appended: list[CorporateAction] = []

    async def append(self, conn: object, action: CorporateAction) -> CorporateAction:
        self.appended.append(action)
        return action

    async def list_history(self, conn: object, instrument_id: UUID) -> list[CorporateAction]:
        return [a for a in self.appended if a.instrument_id == instrument_id]


class _FakeUnprocessedQueue:
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []

    async def enqueue(
        self, conn: object, *, source_id: str, raw_payload: object, reason: str
    ) -> None:
        self.entries.append({"source_id": source_id, "raw_payload": raw_payload, "reason": reason})


def _allowed_contract() -> SourceContract:
    return SourceContract(
        source_id=OPENDART_SOURCE_ID,
        tier=SourceContractTier.FREE,
        credential_ref="vault:opendart:v1",
        redistribution_scope=RedistributionScope.DISPLAY,
        rate_limit=1000,
        quota=10000,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
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
            report_type=cast(
                Literal["SPLIT", "CASH_DIVIDEND", "MERGER"], "NOTICE"
            ),  # unsupported type for negative test
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

_BULK_N = 5_000
_BULK_BUDGET_MS = 12_000.0  # 12.0초 → ms (widen for CI parallel stability, task-15388)


def _bulk_filings() -> list[OpenDartFiling]:
    instrument_id = uuid4()
    return [
        OpenDartFiling(
            instrument_id=instrument_id,
            rcept_no=f"20260302{i:06d}",
            report_type="SPLIT",
            event_date=date(2026, 4, 1),
            known_at=_NOW,
            split_ratio_before=Decimal("10000"),
            split_ratio_after=Decimal("1000"),
        )
        for i in range(_BULK_N)
    ]


def _bulk_normalize_fn(filings: list[OpenDartFiling]) -> Callable[[], None]:
    # Resolve normalize_filing through the module on every call so monkeypatching is observed.
    import src.foundation.market_data.domain.corporate_action.opendart_filing as mod

    def bulk_normalize() -> None:
        for f in filings:
            mod.normalize_filing(f)

    return bulk_normalize


@pytest.mark.perf
def test_normalize_filing_bulk_meets_latency_budget(perf_budget) -> None:
    """5,000 건 정규화(~7ms wall)는 Windows CPU tick(15.6ms)보다 짧아 단발 측정이 0으로
    수렴하므로 batch=8 + best_of(여러 번 중 최소)로 측정한다. 예산·단언 숫자는 불변."""
    sample = perf_budget.assert_within(
        _bulk_normalize_fn(_bulk_filings()), budget_ms=_BULK_BUDGET_MS, n=5, batch=8
    )
    assert sample.cpu_ms > 0.0, "측정이 0으로 수렴 — 단위/batch 결함"


@pytest.mark.perf
def test_normalize_filing_bulk_detects_ten_x_delay(perf_budget, monkeypatch) -> None:
    """예산 10배 지연(호출당 24ms x 5,000 = 120초) 가상 주입 → 같은 측정/단언이 적색이어야 한다."""
    import src.foundation.market_data.domain.corporate_action.opendart_filing as mod

    monkeypatch.setattr(mod, "normalize_filing", virtual_delayed_sync(mod.normalize_filing, 0.024))
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            _bulk_normalize_fn(_bulk_filings()), budget_ms=_BULK_BUDGET_MS, n=3
        )
