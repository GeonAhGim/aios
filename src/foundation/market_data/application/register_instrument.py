"""LA-14 — 인스트루먼트 등록·생애주기 전이 유스케이스 + 감사 이벤트 1:1.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.2, §9.2 LA-14.

두 함수 모두 자체 `pool`에서 커넥션·트랜잭션을 열고(scaffold 시그니처
`register_instrument(cmd, *, refs, audit, pool)`), 그 안에서 도메인 쓰기와
`AuditEventRepository.append_event_in`(L0-4)을 함께 호출한다 — 도중에 무엇
하나라도 실패해 트랜잭션이 롤백되면 감사 이벤트도 함께 사라진다(§9 LA-14
DoD "감사 이벤트 1:1").

심볼 정규화(`to_canonical`)와 생애주기 상태기계(`transition`)는 LA-7이
이미 순수 함수로 갖고 있다 — 이 파일은 그 판정 결과에 감사를 붙이고
가드(중복 등록, RENAME 대상 심볼 사용 중 여부)를 얹을 뿐, 규칙 자체를
다시 구현하지 않는다.

`ReferenceRepository`(LA-9 포트)에는 `instrument_id` 단일 조회 메서드가
없다(venue+canonical_symbol+시각 조합인 `get_instrument`만 있음, LA-12
결정 — 이 리프에서 포트를 새로 만들지 않는다). 그래서 `apply_lifecycle_event`
는 호출자가 이미 갖고 있는 현재 상태(`current: InstrumentRef`)를 인자로
받는다 — `record_fill`이 `asset_class`를 키워드 인자로 받는 것과 같은
이유(저장할 곳/조회할 포트가 아직 없는 값은 호출자가 넘긴다).

§4.2 "DELISTED | * | — | 거부 | — | outcome=DENIED" 행: `lifecycle.transition`
이 `LifecycleTransitionError`를 던지면(DELISTED에서 나가는 전이는 표에
없음) 도메인 쓰기는 건너뛰고 `outcome=DENIED` 감사 이벤트만 커밋한 뒤
그 예외를 다시 던진다 — 이 함수가 트랜잭션을 직접 여는 쪽이라(`post_entry`
처럼 호출자 `conn`을 받는 게 아니다) DENIED 감사를 살리려면 예외를
`async with` 블록 **밖**에서 던져야 한다(블록 안에서 던지면 방금 커밋하려던
DENIED 행도 함께 롤백된다).

§4.2 DELIST 가드("열린 포지션 0 또는 강제 플래그")는 B(positions) 컨텍스트
조회가 필요하다 — LA-14는 LA-12/L0-4에만 의존하고 positions에는 의존하지
않으므로(task-616 depends_on) 이 가드는 이 리프의 범위 밖이다(**미검증**,
후속 리프에서 positions 포트를 주입받아 추가해야 한다).
"""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.market_data.contracts.v1 import (
    InstrumentRef,
    LifecycleEventCommand,
    RegisterInstrumentCommand,
)
from src.foundation.market_data.domain.reference.lifecycle import (
    LifecycleTransitionError,
    transition,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import to_canonical
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = [
    "AuditAppender",
    "RenameSymbolInUseError",
    "apply_lifecycle_event",
    "register_instrument",
]

_ACTIONS: dict[str, str] = {
    "LIST": "instrument.listed",
    "SUSPEND": "instrument.suspended",
    "RESUME": "instrument.resumed",
    "DELIST": "instrument.delisted",
    "RENAME": "instrument.renamed",
}


class RenameSymbolInUseError(Exception):
    """§4.2 RENAME 가드 `new_venue_symbol 미사용 중` 위반."""


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


async def register_instrument(
    pool: asyncpg.Pool,
    cmd: RegisterInstrumentCommand,
    *,
    refs: ReferenceRepository,
    audit: AuditAppender,
) -> InstrumentRef:
    """신규 등록. 같은 (venue, venue_symbol) 중복은 `refs.register`가
    `DuplicateInstrumentError`로 즉시 거부한다 — 아무 쓰기도 일어나기 전이라
    감사 이벤트도 남기지 않는다(DELIST된 심볼이라도 같은 venue_symbol
    재등록은 이 경로로 그대로 거부된다)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument = await refs.register(conn, cmd)

        payload: dict[str, object] = {
            "instrument_id": str(instrument.instrument_id),
            "venue": instrument.venue.value,
            "canonical_symbol": instrument.canonical_symbol,
            "venue_symbol": instrument.venue_symbol,
            "listed_at": instrument.listed_at.isoformat(),
        }
        assert_safe_payload(payload)
        await audit.append_event_in(
            conn,
            tenant_id=None,
            aggregate_type="md_instrument",
            aggregate_id=instrument.instrument_id,
            aggregate_revision=None,
            action="instrument.registered",
            outcome=Outcome.SUCCESS,
            actor_subject_id=cmd.actor_subject_id,
            trace_id=cmd.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

    return instrument


async def apply_lifecycle_event(
    pool: asyncpg.Pool,
    cmd: LifecycleEventCommand,
    *,
    current: InstrumentRef,
    refs: ReferenceRepository,
    audit: AuditAppender,
) -> InstrumentRef:
    """§4.2 상태기계 전이 + 감사(성공/거부 모두 정확히 하나)."""
    if current.instrument_id != cmd.instrument_id:
        raise ValueError("current.instrument_id가 cmd.instrument_id와 다릅니다")

    denial: Exception | None = None
    new_status = current.status

    async with pool.acquire() as conn, conn.transaction():
        try:
            new_status = transition(current.status, cmd.event)
            if cmd.event == "RENAME":
                if not cmd.new_venue_symbol:
                    raise RenameSymbolInUseError("RENAME에는 new_venue_symbol이 필요합니다")
                # 형식 검증만(SymbolNormalizationError) — 조회·저장은 원시 심볼을 그대로 쓴다.
                to_canonical(current.venue, cmd.new_venue_symbol)
                clash = await refs.get_instrument(
                    conn, current.venue, cmd.new_venue_symbol, cmd.effective_at
                )
                if clash is not None:
                    raise RenameSymbolInUseError(
                        f"new_venue_symbol 사용 중: venue={current.venue.value} "
                        f"symbol={cmd.new_venue_symbol!r}"
                    )
                await refs.add_alias(
                    conn, current.instrument_id, current.venue, cmd.new_venue_symbol
                )
        except (LifecycleTransitionError, RenameSymbolInUseError) as exc:
            denial = exc

        payload: dict[str, object] = {
            "instrument_id": str(current.instrument_id),
            "event": cmd.event,
            "from_status": current.status.value,
            "source_ref": cmd.source_ref,
        }
        if denial is None:
            payload["to_status"] = new_status.value
        assert_safe_payload(payload)
        await audit.append_event_in(
            conn,
            tenant_id=None,
            aggregate_type="md_instrument",
            aggregate_id=current.instrument_id,
            aggregate_revision=None,
            action=_ACTIONS[cmd.event],
            outcome=Outcome.DENIED if denial is not None else Outcome.SUCCESS,
            actor_subject_id=cmd.actor_subject_id,
            trace_id=cmd.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

    if denial is not None:
        raise denial

    return current.model_copy(update={"status": new_status})
