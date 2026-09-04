"""LA-16 — 틱 인제스트: trade_id 단조성·시각 역행 검사 → 저장 → 감사.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-16.

캔들(LA-15)과 달리 세션·갭 검사가 없다(틱은 24시간 스트림) — 유일한
게이트는 같은 (venue, instrument_id)에서 trade_id·traded_at이 뒤로 가지
않는지뿐이다. 위반 시 배치 전체를 REJECT한다(부분 저장 금지, §9 DoD) —
틱은 격리 테이블이 없어(LA-11) 캔들처럼 개별 레코드 격리를 하지 않는다.

`contracts/v1.py`·마이그레이션·`BatchRepository`는 수정 불가(task-842
decision). `IngestSource`에 틱 조회 메서드가 없고 이 리프도 추가하지
않으므로, 이미 올바른 `instrument_id`로 채워진 `TickRecord` 목록을
호출부가 직접 넘긴다(재키잉 없음). `IngestTicksCommand`도 그래서 공개
계약이 아니라 이 모듈 전용 입력이다 — 심볼 상태 검사·as-of 조회처럼
참조데이터에 얽힌 것은 범위 밖이다.

"직전 저장분 이하 trade_id는 REJECT"(§9)를 같음까지 포함해 그대로
적용하면 재실행 멱등(같은 배치 재수집 시 `md_tick` ON CONFLICT DO
NOTHING으로 조용히 성공, LA-15 캔들 재수집과 같은 원칙)이 깨진다 —
배치 안에서 이미 `md_tick`에 있는 trade_id는 "재수집"으로 보고 역행
검사에서 제외하고, 아직 없는 trade_id만 지금까지 본 최댓값(직전
저장분 포함)보다 작으면 역행으로 REJECT한다.

트랜잭션 경계는 LA-15와 동일하게 저장·배치 기록·감사 이벤트를 하나로
묶어 감사 실패 시 전부 롤백한다. 같은 (venue, instrument_id) 동시 호출이
같은 "직전 저장분"을 읽고 둘 다 통과하는 경쟁을 막기 위해 읽기 전
`pg_advisory_xact_lock`으로 트랜잭션이 끝날 때까지 직렬화한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.market_data.application.ingest_candles import AuditAppender
from src.foundation.market_data.contracts.v1 import (
    QualityIssue,
    QualityIssueType,
    QualityVerdict,
    Severity,
    TickIngestBatchResult,
    TickRecord,
    Venue,
    Verdict,
)
from src.foundation.market_data.domain.lineage import batch_hash, request_fingerprint
from src.foundation.market_data.ports.batch_repository import BatchRepository

__all__ = ["IngestTicksCommand", "ingest_ticks"]


@dataclass(frozen=True)
class IngestTicksCommand:
    """`ticks`는 이미 올바른 `instrument_id`로 채워진(모듈 docstring
    참고) 비어 있지 않은, 같은 (venue, instrument_id)의 목록이어야 한다."""

    tenant_id: UUID | None
    source: str
    ticks: list[TickRecord]
    trace_id: UUID


def _parse_trade_id(trade_id: str) -> int | None:
    """숫자가 아닌 trade_id(거래소별 형식 미검증)는 `None` — 이때 trade_id
    축은 검사에서 빠지고 시각 축만 역행을 판단한다."""
    try:
        return int(trade_id)
    except ValueError:
        return None


async def _last_stored(
    conn: asyncpg.Connection, venue: Venue, instrument_id: UUID
) -> tuple[int | None, datetime | None]:
    """최댓값 `traded_at`과 그 시각에 동시 체결된 모든 trade_id 중 최댓값을
    baseline으로 삼는다. 같은 트랜잭션에서 저장된 동시 체결 틱은 `created_at`도
    동일해(Postgres `now()`는 트랜잭션 시작 시각으로 고정) `traded_at DESC,
    created_at DESC LIMIT 1`로는 어느 행이 뽑힐지 비결정적이라 더 작은
    trade_id가 baseline이 될 수 있다 — 그러면 실제로는 역행인 배치가
    통과된다(QA 발견, task-1004)."""
    max_traded_at = await conn.fetchval(
        "SELECT MAX(traded_at) FROM md_tick WHERE venue = $1 AND instrument_id = $2",
        venue.value,
        instrument_id,
    )
    if max_traded_at is None:
        return None, None
    rows = await conn.fetch(
        "SELECT trade_id FROM md_tick WHERE venue = $1 AND instrument_id = $2 AND traded_at = $3",
        venue.value,
        instrument_id,
        max_traded_at,
    )
    max_trade_id: int | None = None
    for row in rows:
        parsed = _parse_trade_id(row["trade_id"])
        if parsed is not None and (max_trade_id is None or parsed > max_trade_id):
            max_trade_id = parsed
    return max_trade_id, max_traded_at


async def _known_trade_ids(
    conn: asyncpg.Connection, venue: Venue, instrument_id: UUID, ticks: list[TickRecord]
) -> set[str]:
    rows = await conn.fetch(
        "SELECT trade_id FROM md_tick WHERE venue = $1 AND instrument_id = $2 "
        "AND trade_id = ANY($3::text[])",
        venue.value,
        instrument_id,
        [t.trade_id for t in ticks],
    )
    return {row["trade_id"] for row in rows}


def _first_regression(
    ticks: list[TickRecord],
    baseline_trade_id: int | None,
    baseline_traded_at: datetime | None,
) -> QualityIssue | None:
    """실행 최댓값(baseline 포함) 대비 trade_id/traded_at이 엄격히 감소하는
    첫 지점을 순서대로 찾는다. 같음은 최댓값을 갱신하지 않을 뿐 위반이 아니다."""
    max_trade_id = baseline_trade_id
    max_traded_at = baseline_traded_at
    for tick in ticks:
        if max_traded_at is not None and tick.traded_at < max_traded_at:
            return QualityIssue(
                type=QualityIssueType.TIME_MISALIGNED,
                severity=Severity.REJECT,
                open_time=tick.traded_at,
                detail={"reason": "traded_at_regression", "trade_id": tick.trade_id},
            )
        trade_id_int = _parse_trade_id(tick.trade_id)
        if trade_id_int is not None and max_trade_id is not None and trade_id_int < max_trade_id:
            return QualityIssue(
                type=QualityIssueType.TIME_MISALIGNED,
                severity=Severity.REJECT,
                open_time=tick.traded_at,
                detail={"reason": "trade_id_regression", "trade_id": tick.trade_id},
            )
        if max_traded_at is None or tick.traded_at > max_traded_at:
            max_traded_at = tick.traded_at
        if trade_id_int is not None and (max_trade_id is None or trade_id_int > max_trade_id):
            max_trade_id = trade_id_int
    return None


async def _store_ticks(conn: asyncpg.Connection, ticks: list[TickRecord]) -> None:
    await conn.executemany(
        "INSERT INTO md_tick (venue, instrument_id, trade_id, price, quantity, side, traded_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) "
        "ON CONFLICT (venue, instrument_id, trade_id, traded_at) DO NOTHING",
        [
            (t.venue.value, t.instrument_id, t.trade_id, t.price, t.quantity, t.side, t.traded_at)
            for t in ticks
        ],
    )


async def ingest_ticks(
    cmd: IngestTicksCommand,
    *,
    batches: BatchRepository,
    audit: AuditAppender,
    pool: asyncpg.Pool,
) -> TickIngestBatchResult:
    if not cmd.ticks:
        raise ValueError("빈 틱 배치는 처리할 수 없다")

    venue = cmd.ticks[0].venue
    instrument_id = cmd.ticks[0].instrument_id
    range_start = min(t.traded_at for t in cmd.ticks)
    range_end = max(t.traded_at for t in cmd.ticks)
    hash_of_batch = batch_hash(cmd.ticks)
    fingerprint = request_fingerprint(
        cmd.source,
        {
            "venue": venue.value,
            "instrument_id": str(instrument_id),
            "range_start": range_start.isoformat(),
            "range_end": range_end.isoformat(),
            "batch_hash": hash_of_batch,
        },
    )
    batch_id = uuid4()

    async with pool.acquire() as conn, conn.transaction():
        # 동시 배치의 "직전 저장분" 경쟁 방지(모듈 docstring 참고).
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))", f"{venue.value}:{instrument_id}"
        )
        known = await _known_trade_ids(conn, venue, instrument_id, cmd.ticks)
        new_ticks = [t for t in cmd.ticks if t.trade_id not in known]
        baseline_trade_id, baseline_traded_at = await _last_stored(conn, venue, instrument_id)
        regression = _first_regression(new_ticks, baseline_trade_id, baseline_traded_at)
        is_stored = regression is None

        verdict = QualityVerdict(
            verdict=Verdict.ACCEPT if is_stored else Verdict.REJECT,
            accepted=len(cmd.ticks) if is_stored else 0,
            quarantined=0,
            rejected=0 if is_stored else len(cmd.ticks),
            issues=[] if regression is None else [regression],
        )

        payload: dict[str, object] = {
            "batch_id": str(batch_id),
            "venue": venue.value,
            "instrument_id": str(instrument_id),
            "range_start": range_start.isoformat(),
            "range_end": range_end.isoformat(),
            "verdict": verdict.verdict.value,
            "accepted": verdict.accepted,
            "rejected": verdict.rejected,
        }
        assert_safe_payload(payload)
        event = await audit.append_event_in(
            conn,
            tenant_id=cmd.tenant_id,
            aggregate_type="md_ingest_batch_tick",
            aggregate_id=batch_id,
            aggregate_revision=None,
            action="market_data.ticks_ingested",
            outcome=Outcome.SUCCESS if is_stored else Outcome.DENIED,
            actor_subject_id=None,
            trace_id=cmd.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

        result = TickIngestBatchResult(
            batch_id=batch_id,
            tenant_id=cmd.tenant_id,
            source=cmd.source,
            venue=venue,
            instrument_id=instrument_id,
            range_start=range_start,
            range_end=range_end,
            request_fingerprint=fingerprint,
            verdict=verdict,
            batch_hash=hash_of_batch,
            audit_event_id=event.id,
        )
        created = await batches.create_tick_batch(conn, result)

        if is_stored:
            await _store_ticks(conn, cmd.ticks)

    return created
