"""LA-14 — venue 거래 캘린더 연도 단위 동기화 유스케이스 + 감사 이벤트 1:1.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-14, §10 R4.

yaml 파싱은 LA-12 `adapters/yaml_calendar_source.load_calendar`가 전담한다
(재구현 금지) — 호출자가 그 결과(`days`)를 미리 만들어 이 함수에 넘긴다
(scaffold 시그니처 `sync_calendar(venue, year, days, *, cal, audit, pool)`
그대로). `CalendarRepository.upsert_days`(LA-12)가 이미 `(venue,
trade_date)` 단위로 멱등(`ON CONFLICT ... DO UPDATE`)이므로, 이 함수는 그
위에 트랜잭션 경계 안에서 감사 이벤트 하나만 덧붙인다.

`upsert_days` 자신도 `day.venue != venue` 불일치를 거부하지만(어댑터
`ValueError`), 이 함수는 그 검증을 트랜잭션·감사 이벤트 **이전**에
먼저 해서(fail-fast) 잘못된 인자로 커넥션을 점유하지 않는다 — 아무 것도
쓰지 않았으니 감사 이벤트도 없다(§9 LA-14 "감사 이벤트 1:1"의 자연스러운
귀결: 시도조차 하지 않은 쓰기에는 이벤트도 없다).

`md_venue_calendar_day`에는 자체 UUID PK가 없다(venue+trade_date 복합키,
LA-10). 감사 이벤트는 `aggregate_id: UUID`를 요구하므로(79번 §1), 이
동기화 "실행" 자체를 하나의 집합체로 보고 `(venue, year)`에서 결정론적으로
파생한 UUID5를 쓴다 — 매번 같은 (venue, year)에 같은 aggregate_id가
나오므로 그 venue·연도의 캘린더 감사 이력을 이 id로 계속 추적할 수 있다.
"""
from __future__ import annotations

import uuid
from typing import Protocol
from uuid import UUID

import asyncpg

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.market_data.contracts.v1 import CalendarDay, Venue
from src.foundation.market_data.ports.calendar_repository import CalendarRepository

__all__ = ["AuditAppender", "CalendarVenueMismatchError", "calendar_aggregate_id", "sync_calendar"]

_NAMESPACE = uuid.UUID("6f2a9d5e-0f0a-4b1a-9a3d-000000000000")


class CalendarVenueMismatchError(Exception):
    """`days` 중 인자 `venue`와 다른 원소가 있음 — `upsert_days`에 위임하지
    않고 여기서 먼저 fail-closed로 걸러 잘못된 데이터가 반쯤(트랜잭션 진입
    후) 반영되지 않게 한다."""


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


def calendar_aggregate_id(venue: Venue, year: int) -> UUID:
    """(venue, year) → 결정론적 UUID5. 같은 인자는 항상 같은 id를 낸다."""
    return uuid.uuid5(_NAMESPACE, f"{venue.value}:{year}")


async def sync_calendar(
    pool: asyncpg.Pool,
    venue: Venue,
    year: int,
    days: list[CalendarDay],
    *,
    actor_subject_id: UUID,
    trace_id: UUID,
    cal: CalendarRepository,
    audit: AuditAppender,
) -> int:
    in_year = [d for d in days if d.trade_date.year == year]
    for day in in_year:
        if day.venue is not venue:
            raise CalendarVenueMismatchError(
                f"venue 불일치: 인자={venue.value} day.venue={day.venue.value}"
            )

    async with pool.acquire() as conn, conn.transaction():
        await cal.upsert_days(conn, venue, in_year)

        payload: dict[str, object] = {
            "venue": venue.value,
            "year": year,
            "day_count": len(in_year),
            "sources": sorted({d.source for d in in_year}),
        }
        assert_safe_payload(payload)
        await audit.append_event_in(
            conn,
            tenant_id=None,
            aggregate_type="md_venue_calendar",
            aggregate_id=calendar_aggregate_id(venue, year),
            aggregate_revision=None,
            action="market_data.calendar_synced",
            outcome=Outcome.SUCCESS,
            actor_subject_id=actor_subject_id,
            trace_id=trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

    return len(in_year)
