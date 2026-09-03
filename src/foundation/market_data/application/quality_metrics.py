"""LA-18 — 최근 배치·스테일 상태를 관측성 게이지로 내보낸다.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.1(STALE 강제
위치: 스케줄러), §7(`md_staleness_seconds`, `md_gap_ratio_24h` 게이지), §9.2
LA-18.

STALE 판정은 `domain/quality/stale_detector.detect_stale`(LA-5)를 그대로
쓴다 — `ingest_candles`(LA-15)가 이 판정을 파이프라인에서 뺀 이유(그 모듈
docstring 참조)가 바로 이 함수다. 세션 열림 여부(§4.1 "세션이 열려 있고")는
`ingest_candles._sessions_in_range`와 같은 근거로: 크립토(BITGET,
continuous)는 캘린더 조회 없이 상수 스펙만으로, KRX/US는
`CalendarRepository`(LA-12)로 조회한다.

편차 1: 명세 §2.2 표는 이 모듈의 의존(포트)을 "batches, store"로만 적지만,
STALE 판정에 세션 열림 여부가 필요해(§4.1) `cal: CalendarRepository`를
추가로 받는다 — 재구현 금지 원칙상 세션 판정을 여기서 다시 만들지 않기
위해서다.

편차 2: "어떤 (venue, instrument, timeframe) 시계열을 볼지" 결정하는
포트가 이 리프 범위(LA-9 포트 5개)에 없다 — `ReferenceRepository`에는
전체 목록 조회가, `BatchRepository`에는 최근 배치 목록 조회가 없다.
`LedgerIntegrityScheduler._fetch_payout_capture_candidates`와 같은 선례를
따라 최근 24시간 안에 배치가 있었던 시계열을 `md_ingest_batch`에 직접
SQL로 조회해 대상으로 삼는다(포트 하나에 담기 애매한 횡단 목록 조회는
application 계층이 `pool`로 직접 한다는 이 코드베이스의 기존 패턴).

`gap_ratio_24h`/`reject_ratio_24h`는 정확한 24시간 누적이 아니라 각
시계열의 **가장 최근 배치**(`batches.get()`이 재구성한 `QualityVerdict`)
기준이다 — 여러 배치에 걸친 누적 집계를 시도하면 배치마다
`batches.get()`을 반복 호출해야 해서(각 호출이 `md_candle`/
`md_quarantine_candle` COUNT 2회 + 이슈 전체 조회) 시계열 수가 늘수록
스케줄러 주기 비용이 선형이 아니라 눈덩이가 된다. "최근 배치 하나"는
근사치이지만 §4.1 GAP/REJECT 판정 자체가 배치 단위로 이뤄지므로 이번
배치 상태를 그대로 반영한다는 점에서 방향은 맞다(Draft, §10 미기재).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import asyncpg

from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.market_data.contracts.v1 import (
    DataQualityMetrics,
    QualityIssueType,
    SeriesKey,
    Severity,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.quality.stale_detector import detect_stale
from src.foundation.market_data.ports.batch_repository import BatchRepository
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore

__all__ = ["Clock", "export_quality_metrics"]

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

_ACTIVITY_WINDOW = timedelta(hours=24)
_GAUGE_LABELS = ("venue", "instrument_id", "timeframe")


async def _session_open(
    conn: asyncpg.Connection, venue: Venue, at: datetime, cal: CalendarRepository
) -> bool:
    spec = KNOWN_SESSIONS[venue.value]
    if spec.continuous:
        return VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec).is_open(at)
    calendar = await cal.load(conn, venue, at.astimezone(spec.tz).year)
    return calendar.is_open(at)


async def _active_series(
    conn: asyncpg.Connection, window_start: datetime
) -> list[SeriesKey]:
    """최근 `_ACTIVITY_WINDOW` 안에 `md_ingest_batch` 행이 하나라도 있는 시계열."""
    rows = await conn.fetch(
        "SELECT DISTINCT venue, instrument_id, timeframe FROM md_ingest_batch "
        "WHERE created_at >= $1",
        window_start,
    )
    return [
        SeriesKey(
            venue=Venue(row["venue"]),
            instrument_id=row["instrument_id"],
            timeframe=Timeframe(row["timeframe"]),
        )
        for row in rows
    ]


async def _latest_batch(
    conn: asyncpg.Connection, key: SeriesKey, window_start: datetime
) -> tuple[UUID, UUID | None] | None:
    """`(batch_id, tenant_id)` — 스케줄러는 전 tenant를 훑는 내부 잡이라
    이 조회 자체는 tenant로 좁히지 않는다(§4.1 편차 2). 뒤이은
    `batches.get()` 호출에 넘길 소유자 `tenant_id`를 같이 반환한다 —
    LA-22가 `get()`에 tenant 필터를 추가한 뒤로는 아무 tenant_id나 넘기면
    "존재 비노출"에 걸려 자기 배치도 못 읽으므로."""
    row = await conn.fetchrow(
        "SELECT id, tenant_id FROM md_ingest_batch WHERE venue = $1 AND instrument_id = $2 "
        "AND timeframe = $3 AND created_at >= $4 ORDER BY created_at DESC LIMIT 1",
        key.venue.value,
        key.instrument_id,
        key.timeframe.value,
        window_start,
    )
    if row is None:
        return None
    return cast("UUID", row["id"]), cast("UUID | None", row["tenant_id"])


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return Decimal(numerator) / Decimal(denominator)


async def _export_one(
    conn: asyncpg.Connection,
    key: SeriesKey,
    window_start: datetime,
    now: datetime,
    *,
    batches: BatchRepository,
    store: CandleStore,
    cal: CalendarRepository,
    registry: MetricsRegistry,
) -> DataQualityMetrics | None:
    last_open_time = await store.last_open_time(conn, key)
    if last_open_time is None:
        logger.warning(
            "quality_metrics: venue=%s instrument_id=%s tf=%s 저장된 캔들 없음 — 이번 주기 스킵",
            key.venue.value,
            key.instrument_id,
            key.timeframe.value,
        )
        return None

    latest = await _latest_batch(conn, key, window_start)
    last_batch_id = latest[0] if latest is not None else None
    gap_count = 0
    reject_count = 0
    record_count = 0
    if latest is not None:
        batch_id, batch_tenant_id = latest
        batch = await batches.get(conn, batch_id, batch_tenant_id)
        if batch is not None:
            verdict = batch.verdict
            record_count = verdict.accepted + verdict.quarantined + verdict.rejected
            # `verdict.rejected`(재구성값)는 REJECT 캔들도 격리 테이블에 저장되는
            # 현 어댑터 동작상 사실상 항상 0이다(postgres_batch_repository.py
            # docstring 참조) — 대신 이슈 목록에서 REJECT 심각도를 직접 센다.
            # 캔들 하나가 이슈 여러 개(예: high<open과 volume<0 동시 위반)를
            # 낼 수 있어 open_time으로 중복 제거한다.
            gap_count = len(
                {i.open_time for i in verdict.issues if i.type is QualityIssueType.GAP}
            )
            reject_count = len(
                {i.open_time for i in verdict.issues if i.severity is Severity.REJECT}
            )

    staleness_s = int((now - last_open_time).total_seconds())
    session_open = await _session_open(conn, key.venue, now, cal)
    stale_issue = detect_stale(last_open_time, now, key.timeframe, session_open)

    gap_ratio = _ratio(gap_count, gap_count + record_count)
    reject_ratio = _ratio(reject_count, record_count)

    labels = {
        "venue": key.venue.value,
        "instrument_id": str(key.instrument_id),
        "timeframe": key.timeframe.value,
    }
    registry.gauge("md_staleness_seconds", _GAUGE_LABELS).set(float(staleness_s), **labels)
    registry.gauge("md_gap_ratio_24h", _GAUGE_LABELS).set(float(gap_ratio), **labels)
    registry.gauge("md_reject_ratio_24h", _GAUGE_LABELS).set(float(reject_ratio), **labels)
    if stale_issue is not None:
        registry.counter("md_quality_issues_total", ("type", "severity")).inc(
            type=QualityIssueType.STALE.value, severity=Severity.WARN.value
        )

    return DataQualityMetrics(
        key=key,
        staleness_s=staleness_s,
        gap_ratio_24h=gap_ratio,
        reject_ratio_24h=reject_ratio,
        last_batch_id=last_batch_id,
    )


async def export_quality_metrics(
    *,
    batches: BatchRepository,
    store: CandleStore,
    cal: CalendarRepository,
    pool: asyncpg.Pool,
    registry: MetricsRegistry,
    clock: Clock,
) -> list[DataQualityMetrics]:
    """최근 24시간 활동이 있는 시계열마다 스테일·갭·거부 비율 게이지를 갱신한다.

    시계열 하나의 계산 실패(예외)는 로그만 남기고 건너뛴다 — 나머지
    시계열은 계속 처리한다(§9 LA-18 DoD: "심볼 1개 실패가 나머지 차단
    안 함")."""
    now = clock()
    window_start = now - _ACTIVITY_WINDOW

    results: list[DataQualityMetrics] = []
    async with pool.acquire() as conn:
        for key in await _active_series(conn, window_start):
            try:
                metrics = await _export_one(
                    conn, key, window_start, now,
                    batches=batches, store=store, cal=cal, registry=registry,
                )
            except Exception:
                logger.exception(
                    "quality_metrics: venue=%s instrument_id=%s tf=%s 지표 계산 실패 — "
                    "나머지 시계열은 계속 처리",
                    key.venue.value,
                    key.instrument_id,
                    key.timeframe.value,
                )
                continue
            if metrics is not None:
                results.append(metrics)
    return results
