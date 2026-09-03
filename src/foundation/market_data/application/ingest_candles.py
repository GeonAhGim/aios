"""LA-15 — 캔들 인제스트 유스케이스: fetch → 품질 게이트 → 저장/격리 → 감사.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.1, §8.2, §9.2 LA-15.

파이프라인은 기존 순수 도메인(LA-4~8)을 위임만 한다(재구현 금지):
`dedupe`(LA-4) → `check_candle`(LA-4) → `detect_spikes`(LA-6) → `detect_gaps`
(LA-5) → `decide`(LA-6). STALE(LA-5 `detect_stale`)은 §4.1 표의 "강제 위치:
스케줄러" 그대로 이 파이프라인에 넣지 않는다 — LA-18 `quality_metrics`가
저장된 배치를 주기적으로 훑으며 판단할 몫이다(여기서 매 ingest마다 재현하면
백필처럼 `range_end`가 과거인 요청도 항상 STALE WARN이 붙어 verdict가
불필요하게 PARTIAL이 된다).

`IngestSource.fetch_candles`(LA-9 포트, 이미 고정)는 DB를 모르므로 반환하는
`CandleRecord.key.instrument_id`는 의미가 없다(어댑터가 고른 임의 값) —
이 함수가 참조데이터에서 조회한 진짜 `instrument_id`로 무조건 다시 키를
씌운다(어댑터가 어떤 placeholder를 골랐는지 이 함수는 신경 쓰지 않는다).

트랜잭션 경계: 참조데이터·캘린더 조회(읽기 전용)와 소스 fetch(외부 HTTP)는
DB 트랜잭션 밖에서 수행한다 — 느린 외부 I/O 동안 커넥션을 붙잡아 두지
않기 위해서다(LA-14는 전부 DB I/O라 이 구분이 없었다). 저장·배치 기록·감사
이벤트만 하나의 트랜잭션으로 묶는다 — 이 마지막 블록 안에서 무엇 하나라도
실패하면(특히 `audit.append_event_in` 실패 주입) `md_candle`·
`md_quarantine_candle`·`md_ingest_batch`·`md_quality_issue` 전부 롤백된다
(§9 LA-15 DoD).

§4.1 "배치의 REJECT 비율 > 20% → 배치 전체 QUARANTINE(부분 저장 금지)"는
`verdict.decide`가 이미 판정하므로, 그 결과가 QUARANTINE이면 저장 대상으로
골라 둔 "good" 캔들까지 포함해 원본 전체(`rekeyed`)를 격리한다.

알려진 제약: `PostgresReferenceRepository.register()`(LA-12)는 최초 별칭을
venue 원시 심볼 형식(`cmd.venue_symbol`)으로 심는데, `apply_lifecycle_event`
의 RENAME 경로(LA-14)는 canonical 형식으로 새 별칭을 심는다 — 같은
`md_symbol_alias` 테이블에 형식이 섞여 있다(KRX/US는 canonical==venue 원시라
드러나지 않고, BASE/QUOTE 슬래시가 붙는 BITGET에서만 갈린다). 이 함수는
아직 RENAME되지 않은 인스트루먼트를 전제로 `symbol_normalizer.to_venue`로
`cmd.canonical_symbol`을 venue 원시 형식으로 되돌려 조회한다 — RENAME 이후
조회는 이 리프 범위 밖(LA-12/LA-14 별칭 형식 정합화가 선행되어야 한다).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    IngestCandlesCommand,
    QualityIssue,
    SeriesKey,
    SessionWindow,
    Severity,
    SymbolStatus,
    Venue,
    Verdict,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.lineage import batch_hash, request_fingerprint
from src.foundation.market_data.domain.quality.dedupe import dedupe
from src.foundation.market_data.domain.quality.gap_detector import detect_gaps
from src.foundation.market_data.domain.quality.ohlc_sanity import check_candle
from src.foundation.market_data.domain.quality.outlier_detector import detect_spikes
from src.foundation.market_data.domain.quality.verdict import decide
from src.foundation.market_data.domain.reference.symbol_normalizer import to_venue
from src.foundation.market_data.ports.batch_repository import BatchRepository
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.ingest_source import IngestSource
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = [
    "AuditAppender",
    "Clock",
    "SymbolNotTradableError",
    "SymbolUnknownError",
    "ingest_candles",
]

Clock = Callable[[], datetime]

_NOT_TRADABLE = frozenset({SymbolStatus.SUSPENDED, SymbolStatus.DELISTED})


class SymbolUnknownError(Exception):
    """`MD_SYMBOL_UNKNOWN` — `(venue, canonical_symbol)`이 참조데이터에 없음."""


class SymbolNotTradableError(Exception):
    """`MD_SYMBOL_NOT_TRADABLE` — SUSPENDED/DELISTED 심볼은 ingest 거부."""


class AuditAppender(Protocol):
    async def append_event_in(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent: ...


def _bitget_calendar() -> VenueCalendar:
    session = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=session.tz, regular=session)


async def _sessions_in_range(
    conn: asyncpg.Connection,
    venue: Venue,
    start: datetime,
    end: datetime,
    cal: CalendarRepository,
) -> list[SessionWindow]:
    """`[start, end)` 구간과 겹치는 세션만, 그 구간으로 잘라서 반환한다 —
    `gap_detector.detect_gaps`가 세션의 `min(open_at)/max(close_at)`으로 기대
    구간을 재구성하므로, 요청 구간 밖까지 포함된 세션을 그대로 넘기면 애초에
    fetch하지도 않은 시각에 대해 가짜 GAP이 생긴다."""
    tz = KNOWN_SESSIONS[venue.value].tz
    day = start.astimezone(tz).date()
    last_day = end.astimezone(tz).date()
    calendars: dict[int, VenueCalendar] = {}
    windows: list[SessionWindow] = []
    one_day = timedelta(days=1)
    while day <= last_day:
        calendar = calendars.get(day.year)
        if calendar is None:
            calendar = _bitget_calendar() if venue is Venue.BITGET else await cal.load(
                conn, venue, day.year
            )
            calendars[day.year] = calendar
        for window in calendar.sessions_for(day):
            clipped_open = max(window.open_at, start)
            clipped_close = min(window.close_at, end)
            if clipped_open < clipped_close:
                windows.append(
                    SessionWindow(open_at=clipped_open, close_at=clipped_close, kind=window.kind)
                )
        day += one_day
    return windows


def _sort_by_open_time(candles: list[CandleRecord]) -> list[CandleRecord]:
    return sorted(candles, key=lambda c: c.open_time)


async def ingest_candles(
    cmd: IngestCandlesCommand,
    *,
    source: IngestSource,
    store: CandleStore,
    refs: ReferenceRepository,
    cal: CalendarRepository,
    batches: BatchRepository,
    audit: AuditAppender,
    pool: asyncpg.Pool,
    clock: Clock,
) -> IngestBatchResult:
    now = clock()
    lookup_symbol = to_venue(cmd.venue, cmd.canonical_symbol)

    async with pool.acquire() as read_conn:
        instrument = await refs.get_instrument(read_conn, cmd.venue, lookup_symbol, now)
        if instrument is None:
            raise SymbolUnknownError(
                f"참조데이터 없음: venue={cmd.venue.value} canonical={cmd.canonical_symbol!r}"
            )
        if instrument.status in _NOT_TRADABLE:
            raise SymbolNotTradableError(
                f"ingest 불가 상태: instrument_id={instrument.instrument_id} "
                f"status={instrument.status.value}"
            )
        sessions = await _sessions_in_range(
            read_conn, cmd.venue, cmd.range_start, cmd.range_end, cal
        )

    raw_candles = await source.fetch_candles(
        cmd.venue, instrument.venue_symbol, cmd.timeframe, cmd.range_start, cmd.range_end
    )
    key = SeriesKey(
        venue=cmd.venue, instrument_id=instrument.instrument_id, timeframe=cmd.timeframe
    )
    rekeyed = [c.model_copy(update={"key": key}) for c in raw_candles]

    dedupe_result = dedupe(rekeyed)
    sanity_issues: list[QualityIssue] = []
    good: list[CandleRecord] = []
    bad: list[CandleRecord] = []
    for candle in dedupe_result.kept:
        issues = check_candle(candle)
        sanity_issues.extend(issues)
        if any(issue.severity is Severity.REJECT for issue in issues):
            bad.append(candle)
        else:
            good.append(candle)
    good = _sort_by_open_time(good)

    spike_issues = detect_spikes(good)
    gap_issues = detect_gaps(list(dedupe_result.kept), cmd.timeframe, sessions)
    all_issues = [*dedupe_result.issues, *sanity_issues, *spike_issues, *gap_issues]
    verdict_result = decide(all_issues, len(rekeyed))

    batch_id = uuid4()
    is_stored = verdict_result.verdict in (Verdict.ACCEPT, Verdict.PARTIAL)
    stored_range = (good[0].open_time, good[-1].open_time) if is_stored and good else None
    quarantine_candles = (
        [*bad, *dedupe_result.conflicts] if is_stored else (rekeyed if rekeyed else [])
    )

    async with pool.acquire() as conn, conn.transaction():
        # `md_candle`/`md_quarantine_candle`.batch_id는 `md_ingest_batch(id)` FK다 —
        # 배치 행(그리고 그 행이 요구하는 audit_event_id)을 먼저 커밋해야 캔들을
        # 쓸 수 있다. 그래도 전부 한 트랜잭션이라 어느 단계가 실패하든 함께
        # 롤백된다(§9 LA-15 DoD).
        outcome = Outcome.SUCCESS if is_stored else Outcome.DENIED
        payload: dict[str, object] = {
            "batch_id": str(batch_id),
            "venue": cmd.venue.value,
            "canonical_symbol": cmd.canonical_symbol,
            "timeframe": cmd.timeframe.value,
            "range_start": cmd.range_start.isoformat(),
            "range_end": cmd.range_end.isoformat(),
            "verdict": verdict_result.verdict.value,
            "accepted": verdict_result.accepted,
            "quarantined": verdict_result.quarantined,
            "rejected": verdict_result.rejected,
        }
        assert_safe_payload(payload)
        event = await audit.append_event_in(
            conn,
            tenant_id=cmd.tenant_id,
            aggregate_type="md_ingest_batch",
            aggregate_id=batch_id,
            aggregate_revision=None,
            action="market_data.candles_ingested",
            outcome=outcome,
            actor_subject_id=None,
            trace_id=cmd.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

        batch_result = IngestBatchResult(
            batch_id=batch_id,
            tenant_id=cmd.tenant_id,
            source=cmd.venue.value,
            venue=cmd.venue,
            instrument_id=instrument.instrument_id,
            timeframe=cmd.timeframe,
            range_start=cmd.range_start,
            range_end=cmd.range_end,
            request_fingerprint=request_fingerprint(
                cmd.venue.value,
                {
                    "canonical_symbol": cmd.canonical_symbol,
                    "timeframe": cmd.timeframe.value,
                    "range_start": cmd.range_start.isoformat(),
                    "range_end": cmd.range_end.isoformat(),
                },
            ),
            verdict=verdict_result,
            batch_hash=batch_hash(rekeyed),
            audit_event_id=event.id,
            stored_range=stored_range,
        )
        created = await batches.create(conn, batch_result)

        if is_stored:
            await store.upsert_batch(conn, batch_id, good)
        if quarantine_candles:
            await store.quarantine(conn, batch_id, quarantine_candles, all_issues)
        await batches.add_issues(conn, batch_id, all_issues)

    return created
