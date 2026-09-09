"""H-1a — mandate 바인딩 조회 resolver.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-1
(task-2603의 분할, task-3368). 주문 조립부(order_service/foundation_gate.py,
submit_order.py)가 `require_mandate=True`로 전환하려면 "이 주문의 portfolio가
지금 참조할 수 있는 mandate revision이 무엇인가"를 물을 곳이 있어야 한다 —
이 모듈이 그 조회 하나만 한다. 조립부 배선(H-1 본체)과 캐싱(H-11: 상태변경
이벤트 기반 즉시 무효화)은 둘 다 이 리프의 스콥이 아니다 — 매 호출마다 DB를
직접 읽는다.

`portfolio_id`만 받고 `tenant_id`는 받지 않는다: 지금 유일한 채번 경로인 FA-1
`src/foundation/entities/domain/defaults.py`의 `default_portfolio_id(user_id)`가
고정 네임스페이스 UUIDv5로 사용자마다 다른 값을 결정론적으로 만들어, 현재
스키마(`portfolio_mandate` `UNIQUE(tenant_id, portfolio_id)`)에서도
`portfolio_id` 하나로 이미 소유 tenant가 사실상 특정된다. **미검증**: FA-0b가
스키마 상으로는 한 tenant가 여러 portfolio_id를 명시적으로 발급받는 것도
허용한다 — 그 경로가 실제로 쓰이기 시작하면 이 가정이 깨지고, 이 함수도
`tenant_id`를 받도록 바뀌어야 한다.

`MandateRevisionRef.status`의 세 값은 `application/evaluate_policy.py`의
분기(PAUSED는 `PAUSE_REQUIRED`로 별도 취급, 그 외 ACTIVE가 아닌 모든 상태는
`STATE_NO_ACTIVE_MANDATE`로 뭉뚱그림)와 같은 3분류를 그대로 타입으로 옮긴
것이다 — `SUPERSEDED`/`CANCELLED`/`DRAFT`/`PROPOSED` 모두 "이 포인터는 더 이상
유효한 위임이 아니다"라는 같은 결론이라 `EXPIRED` 하나로 묶는다(그중 어느
state인지는 `MandateRevisionRef.revision.state`에 그대로 남아 있어 호출부가
원하면 더 세분화할 수 있다). `portfolio_mandate` 행 자체가 없거나
`active_revision_id`가 NULL이면(아직 어떤 revision도 activate된 적이
없는 mandate 포함) `None`을 반환한다 — "없음"은 결과 타입의 한 갈래가
아니라 부재 자체이므로 `Optional`로 표현한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

import asyncpg

from src.foundation.mandates.domain.models import Autonomy, MandateRevision, MandateRevisionState


class MandateBindingStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class MandateRevisionRef:
    status: MandateBindingStatus
    revision: MandateRevision


def _row_to_revision(row: asyncpg.Record) -> MandateRevision:
    return MandateRevision(
        id=row["id"],
        mandate_id=row["mandate_id"],
        revision_no=row["revision_no"],
        state=MandateRevisionState(row["state"]),
        max_total_exposure_pct=float(row["max_total_exposure_pct"]),
        max_single_instrument_pct=float(row["max_single_instrument_pct"]),
        min_cash_buffer_pct=float(row["min_cash_buffer_pct"]),
        max_daily_loss_pct=float(row["max_daily_loss_pct"]),
        allowed_autonomy=Autonomy(row["allowed_autonomy"]),
        forbidden_assets=tuple(row["forbidden_assets"]),
        revision_hash=row["revision_hash"],
        cooling_off_started_at=row["cooling_off_started_at"],
        created_at=row["created_at"],
        activated_at=row["activated_at"],
    )


def _status_for_state(state: MandateRevisionState) -> MandateBindingStatus:
    if state == MandateRevisionState.ACTIVE:
        return MandateBindingStatus.ACTIVE
    if state == MandateRevisionState.PAUSED:
        return MandateBindingStatus.PAUSED
    return MandateBindingStatus.EXPIRED


async def resolve_mandate_revision(
    pool: asyncpg.Pool, portfolio_id: UUID
) -> MandateRevisionRef | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT r.* FROM portfolio_mandate m "
            "JOIN mandate_revision r ON r.id = m.active_revision_id "
            "WHERE m.portfolio_id = $1",
            portfolio_id,
        )
    if row is None:
        return None
    revision = _row_to_revision(row)
    return MandateRevisionRef(status=_status_for_state(revision.state), revision=revision)
