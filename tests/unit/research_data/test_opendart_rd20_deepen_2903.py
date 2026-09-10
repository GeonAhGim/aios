"""RD-20 opendart_filing / point_in_time / ingest_opendart_filing —
DEEPEN(task-2903, docs/audit/DEPTH_DC_RD.md#1767) D1 -> D2 증빙.

기존 test_opendart_filing.py · test_point_in_time.py ·
test_ingest_opendart_filing.py는 negative 8건+(필드 누락·naive known_at·
source_contract 거부·파싱 실패 큐 적재)과 PIT 정정 무결성을 단건으로
증명했다(D1) — 소급감사(task-2726)에서 "실패주입(DB/네트워크) 없음,
성능단언 없음, 게이트적색 재현 없음"으로 지적됐다(1767행). 이 파일이
그 부족분을 채운다. 도메인·유스케이스 소스는 무수정 — 새 기능 없음,
깊이만 올림.

1. 실패 주입 — filings.append / unprocessed.enqueue 가 DB·네트워크 장애로
   터질 때 예외가 조용히 삼켜지지 않고 그대로 전파되는지, 그리고 장애
   직전까지의 부분 상태가 남지 않는지(append 실패 시 큐 비어 있음,
   enqueue 실패 시 append 미호출).
2. 성능 단언 — 대량(5,000건) 정규화와 대량 정정 이력에 대한 resolve_as_of
   가 절대시간 예산 내인지.
3. 게이트 적색 재현 — 동일 공시를 시간축으로 재생한다: 미등록 계약 거부
   -> 허용+파싱실패 큐 -> 허용+정규화 적재 -> 정정 공시 새 행 + PIT.
   각 단계에서 이전 사유·값이 다음으로 새지 않음을 증명한다.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.market_data.contracts.v1 import CorporateAction
from src.foundation.market_data.domain.corporate_action.opendart_filing import (
    FilingParseError,
    OpenDartFiling,
    normalize_filing,
)
from src.foundation.market_data.domain.corporate_action.point_in_time import resolve_as_of
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
_EX_DATE = date(2026, 4, 1)


class _FakeSourceContractRepo:
    def __init__(self, contract: SourceContract | None) -> None:
        self._contract = contract

    async def get(self, conn, source_id: str):
        return self._contract if source_id == OPENDART_SOURCE_ID else None


class _FakeFilingRepo:
    def __init__(self, *, boom: Exception | None = None) -> None:
        self.appended: list[CorporateAction] = []
        self._boom = boom

    async def append(self, conn, action: CorporateAction) -> CorporateAction:
        if self._boom is not None:
            raise self._boom
        self.appended.append(action)
        return action

    async def list_history(self, conn, instrument_id):
        return [a for a in self.appended if a.instrument_id == instrument_id]


class _FakeUnprocessedQueue:
    def __init__(self, *, boom: Exception | None = None) -> None:
        self.entries: list[dict[str, object]] = []
        self._boom = boom

    async def enqueue(self, conn, *, source_id: str, raw_payload, reason: str) -> None:
        if self._boom is not None:
            raise self._boom
        self.entries.append(
            {"source_id": source_id, "raw_payload": raw_payload, "reason": reason}
        )


def _allowed_contract(*, valid_from: datetime | None = None) -> SourceContract:
    return SourceContract(
        source_id=OPENDART_SOURCE_ID,
        tier=SourceContractTier.FREE,
        credential_ref="vault:opendart:v1",
        redistribution_scope=RedistributionScope.DISPLAY,
        rate_limit=1000,
        quota=10000,
        valid_from=valid_from or datetime(2026, 1, 1, tzinfo=timezone.utc),
        valid_to=None,
        capability=SourceCapability(
            asset_classes=frozenset({"EQUITY_KR"}),
            resolutions=frozenset(),
            corporate_actions=True,
        ),
    )


def _split_filing(
    *,
    instrument_id=None,
    rcept_no: str = "20260302000123",
    known_at: datetime = _NOW,
    before: str = "5000",
    after: str = "500",
) -> OpenDartFiling:
    return OpenDartFiling(
        instrument_id=instrument_id or uuid4(),
        rcept_no=rcept_no,
        report_type="SPLIT",
        event_date=_EX_DATE,
        known_at=known_at,
        split_ratio_before=Decimal(before),
        split_ratio_after=Decimal(after),
    )


# ---- 실패 주입 (DB/네트워크 monkeypatch) ----


async def test_append_db_failure_propagates_and_leaves_queue_empty() -> None:
    """정규화까지 성공한 뒤 filings.append가 DB/네트워크 장애로 터지면
    예외가 조용히 None으로 위장되지 않고 전파돼야 하며, 미처리 큐에도
    행이 남지 않아야 한다(부분 성공 위장 금지)."""
    filings = _FakeFilingRepo(boom=ConnectionError("injected append network failure"))
    queue = _FakeUnprocessedQueue()

    with pytest.raises(ConnectionError, match="injected append network failure"):
        await ingest_opendart_filing(
            conn=None,
            filing=_split_filing(),
            source_contracts=_FakeSourceContractRepo(_allowed_contract()),
            filings=filings,
            unprocessed=queue,
            clock=lambda: _NOW,
        )

    assert filings.appended == []
    assert queue.entries == []


async def test_enqueue_db_failure_on_contract_deny_propagates_not_swallowed() -> None:
    """source_contract 거부 경로에서 unprocessed.enqueue가 DB 장애로
    실패하면 조용히 삼켜지지 않고 그대로 전파돼야 한다 — '거부됐지만
    큐에도 못 남긴' 상태가 성공으로 위장되면 안 된다."""
    filings = _FakeFilingRepo()
    queue = _FakeUnprocessedQueue(boom=OSError("injected enqueue disk/network failure"))

    with pytest.raises(OSError, match="injected enqueue disk/network failure"):
        await ingest_opendart_filing(
            conn=None,
            filing=_split_filing(),
            source_contracts=_FakeSourceContractRepo(None),
            filings=filings,
            unprocessed=queue,
            clock=lambda: _NOW,
        )

    assert filings.appended == []
    assert queue.entries == []


async def test_enqueue_db_failure_on_parse_error_propagates_not_swallowed() -> None:
    """파싱 실패 후 큐 적재가 네트워크 장애로 실패해도 FilingParseError를
    삼키고 None을 반환하지 않는다 — enqueue 예외가 위로 전파된다."""
    filings = _FakeFilingRepo()
    queue = _FakeUnprocessedQueue(boom=ConnectionError("injected enqueue connection failure"))
    malformed = OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302000999",
        report_type="SPLIT",
        event_date=_EX_DATE,
        known_at=_NOW,
    )

    with pytest.raises(ConnectionError, match="injected enqueue connection failure"):
        await ingest_opendart_filing(
            conn=None,
            filing=malformed,
            source_contracts=_FakeSourceContractRepo(_allowed_contract()),
            filings=filings,
            unprocessed=queue,
            clock=lambda: _NOW,
        )

    assert filings.appended == []
    assert queue.entries == []


def test_normalize_filing_zero_split_after_raises_not_swallowed() -> None:
    """액면가 after=0(적대적 입력)이 ZeroDivision을 조용히 삼키거나
    ratio=inf로 새지 않고 FilingParseError로 실패해야 한다."""
    filing = OpenDartFiling(
        instrument_id=uuid4(),
        rcept_no="20260302001234",
        report_type="SPLIT",
        event_date=_EX_DATE,
        known_at=_NOW,
        split_ratio_before=Decimal("5000"),
        split_ratio_after=Decimal("0"),
    )
    with pytest.raises(FilingParseError):
        normalize_filing(filing)


# ---- 성능 단언 ----


@pytest.mark.perf
def test_normalize_filing_bulk_meets_latency_budget() -> None:
    """5,000건 공시(다종목·다유형 배치 적재 시나리오) 정규화가 절대시간
    예산 내여야 한다."""
    n = 5_000
    budget_sec = 5.0  # 실측 로컬 <<1s, CI 편차 감안
    instrument_id = uuid4()
    filings = [
        _split_filing(
            instrument_id=instrument_id,
            rcept_no=f"20260302{i:06d}",
            known_at=_NOW + timedelta(seconds=i),
            before="10000",
            after="1000",
        )
        for i in range(n)
    ]

    start = time.perf_counter()
    actions = [normalize_filing(f) for f in filings]
    elapsed = time.perf_counter() - start

    print(f"[RD-20 normalize] {n}건 {elapsed:.3f}s (budget<{budget_sec}s)")
    assert len(actions) == n
    assert all(a.ratio == Decimal("10") for a in actions)
    assert elapsed < budget_sec, (
        f"대량 정규화가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


@pytest.mark.perf
def test_resolve_as_of_large_correction_history_meets_latency_budget() -> None:
    """동일 (instrument, type, ex_date)에 2,000건 정정이 쌓여도 as_of
    조회가 절대시간 예산 내여야 한다(append-only 이력 성장 시나리오)."""
    n = 2_000
    budget_sec = 3.0
    instrument_id = uuid4()
    history = [
        CorporateAction(
            action_type="SPLIT",
            instrument_id=instrument_id,
            ex_date=_EX_DATE,
            ratio=Decimal(str(i + 1)),
            source_ref=f"rcept-{i}",
            known_at=_NOW + timedelta(seconds=i),
        )
        for i in range(n)
    ]
    as_of = _NOW + timedelta(seconds=n // 2)

    start = time.perf_counter()
    for _ in range(200):
        result = resolve_as_of(history, as_of)
    elapsed = time.perf_counter() - start

    print(f"[RD-20 resolve_as_of] {n}행×200회 {elapsed:.3f}s (budget<{budget_sec}s)")
    assert len(result) == 1
    assert result[0].ratio == Decimal(str(n // 2 + 1))
    assert elapsed < budget_sec, (
        f"대량 PIT 조회가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현 — 동일 공시 생애주기 시간축 재생 ----


async def test_ingest_gate_lifecycle_denied_then_parse_fail_then_ok_then_correction() -> None:
    """동일 instrument 공시를 게이트 시간축으로 재생한다.

    1) 미등록 계약 -> 거부·큐 (어댑터/append 미호출)
    2) 허용 + 파싱 실패 -> 큐 (append 미호출)
    3) 허용 + 정상 분할 -> append (ratio=10)
    4) 정정 공시(새 rcept_no) -> 새 행 append, PIT는 정정 전/후 분리
    각 단계의 사유·값이 다음 단계로 새지 않음을 증명한다(게이트 적색).
    """
    instrument_id = uuid4()
    filings = _FakeFilingRepo()
    queue = _FakeUnprocessedQueue()
    clock = lambda: _NOW  # noqa: E731 — 테스트용 고정 시계

    # 1) 미등록
    denied = await ingest_opendart_filing(
        conn=None,
        filing=_split_filing(instrument_id=instrument_id, rcept_no="rcept-1"),
        source_contracts=_FakeSourceContractRepo(None),
        filings=filings,
        unprocessed=queue,
        clock=clock,
    )
    assert denied is None
    assert filings.appended == []
    assert len(queue.entries) == 1
    assert "source_contract_denied" in str(queue.entries[0]["reason"])

    # 2) 허용 + 파싱 실패
    allowed = _FakeSourceContractRepo(_allowed_contract())
    malformed = OpenDartFiling(
        instrument_id=instrument_id,
        rcept_no="rcept-2",
        report_type="CASH_DIVIDEND",
        event_date=_EX_DATE,
        known_at=_NOW,
        # dividend_per_share 누락
    )
    parse_fail = await ingest_opendart_filing(
        conn=None,
        filing=malformed,
        source_contracts=allowed,
        filings=filings,
        unprocessed=queue,
        clock=clock,
    )
    assert parse_fail is None
    assert filings.appended == []
    assert len(queue.entries) == 2
    assert "source_contract_denied" not in str(queue.entries[1]["reason"])
    assert "dividend_per_share" in str(queue.entries[1]["reason"])

    # 3) 정상 분할
    original = await ingest_opendart_filing(
        conn=None,
        filing=_split_filing(
            instrument_id=instrument_id,
            rcept_no="rcept-original",
            before="5000",
            after="500",
        ),
        source_contracts=allowed,
        filings=filings,
        unprocessed=queue,
        clock=clock,
    )
    assert original is not None
    assert original.ratio == Decimal("10")
    assert len(filings.appended) == 1
    assert len(queue.entries) == 2  # 성공 경로는 큐에 추가하지 않음

    # 4) 정정 공시 — UPDATE가 아니라 새 행
    correction_known = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
    correction = await ingest_opendart_filing(
        conn=None,
        filing=_split_filing(
            instrument_id=instrument_id,
            rcept_no="rcept-correction",
            known_at=correction_known,
            before="5000",
            after="1000",  # 5:1로 정정
        ),
        source_contracts=allowed,
        filings=filings,
        unprocessed=queue,
        clock=lambda: correction_known,
    )
    assert correction is not None
    assert correction.ratio == Decimal("5")
    assert len(filings.appended) == 2
    assert {a.source_ref for a in filings.appended} == {
        "rcept-original",
        "rcept-correction",
    }

    before = resolve_as_of(filings.appended, datetime(2026, 3, 5, tzinfo=timezone.utc))
    after = resolve_as_of(filings.appended, datetime(2026, 3, 15, tzinfo=timezone.utc))
    assert [a.ratio for a in before] == [Decimal("10")]
    assert [a.source_ref for a in before] == ["rcept-original"]
    assert [a.ratio for a in after] == [Decimal("5")]
    assert [a.source_ref for a in after] == ["rcept-correction"]
