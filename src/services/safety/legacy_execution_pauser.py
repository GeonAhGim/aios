"""Kill switch 범위 → legacy `strategy_executions` 대상 매핑.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §2 표(R-38).

§3.8 매핑 표의 legacy 열(`strategy_executions` 조건)만 이 모듈의 책임이다 —
같은 표의 paper_control 열은 별도 리프(R-39/R-40 계열)가 맡는다.
`STRATEGY_DEPLOYMENT` scope의 `dep:<uuid>` 형식이 그 예: legacy 테이블에는
대응하는 행이 없다는 뜻이지 매핑 누락이 아니다.

이 함수는 `RUNNING → PAUSED`로만 전이한다(105번 표준의 조건부 UPDATE —
`WHERE status = 'RUNNING'`). 재개 경로가 전혀 없으므로 LIVE 실행을 새로
열거나 기존 PAPER 하드가드를 약화시킬 수 없다 — 정지만 할 수 있는 함수다.
이미 PAUSED인 행은 조건에 안 걸려 RETURNING에도 안 잡히므로, 같은
control_id로 반복 호출해도 매번 안전하게 빈 배치로 수렴한다(멱등).

`control_id`는 현재 어떤 컬럼에도 쓰이지 않는다 — `paused_by_control_id`
컬럼(§3.8 본문이 언급하는 마이그레이션)이 아직 이 저장소에 없다. 감사
로그(구조화 로그)에만 남기고, 컬럼이 생기면 그 UPDATE의 SET 절에
추가하면 된다."""
from __future__ import annotations

import logging
from uuid import UUID

import asyncpg

from src.foundation.risk_gate.domain.models import SafetyScope

logger = logging.getLogger(__name__)


class UnmappedSafetyScopeError(Exception):
    """§3.8 매핑 표에 없는 `SafetyScope` 값 — 조용히 0건으로 넘어가지 않고
    fail-closed로 명시적 실패한다."""


class MalformedScopeRefError(Exception):
    """scope가 요구하는 `scope_ref` 형식(§3.8 "scope_ref 형식" 열)과 다르다."""


async def pause_executions_for_scope(
    conn: asyncpg.Connection,
    scope: SafetyScope,
    scope_ref: str,
    *,
    control_id: UUID,
) -> list[int]:
    """`scope`/`scope_ref`가 가리키는 legacy `strategy_executions` 행 중
    `status='RUNNING'`인 것만 `PAUSED`(`paused_by='SAFETY_LAYER'`)로
    전이하고, 실제로 전이된 행의 `id`만 반환한다(영향 행 수 추정 금지 —
    `UPDATE ... RETURNING id`가 유일한 근거).
    """
    condition, params = _condition_for(scope, scope_ref)
    if condition is None:
        logger.info(
            "pause_executions_for_scope: scope=%s scope_ref=%s control_id=%s는 "
            "legacy strategy_executions 대상이 아닙니다(paper_control 전용) — 0건.",
            scope.value,
            scope_ref,
            control_id,
        )
        return []

    # condition은 _condition_for()가 돌려주는 고정 상수 중 하나다(호출자 입력이 SQL로 안 들어감).
    sql = (
        "UPDATE strategy_executions SET status = 'PAUSED', paused_by = 'SAFETY_LAYER' "  # noqa: S608
        f"WHERE status = 'RUNNING' AND ({condition}) "
        "RETURNING id"
    )
    rows = await conn.fetch(sql, *params)
    paused_ids = [row["id"] for row in rows]
    logger.info(
        "pause_executions_for_scope(scope=%s, scope_ref=%s, control_id=%s): %d건 정지",
        scope.value,
        scope_ref,
        control_id,
        len(paused_ids),
    )
    return paused_ids


def _condition_for(scope: SafetyScope, scope_ref: str) -> tuple[str | None, list[object]]:
    """§3.8 표의 "legacy `strategy_executions` 조건" 열 그대로 매핑한다.

    반환값이 `(None, [])`이면 조건 자체가 없는 게 아니라 "이 scope_ref
    형식은 legacy 테이블 대상이 아니다"(예: `STRATEGY_DEPLOYMENT`의
    `dep:<uuid>`)라는 뜻 — 호출자는 빈 배치로 처리한다.
    """
    if scope == SafetyScope.GLOBAL:
        return "TRUE", []
    if scope == SafetyScope.PROVIDER:
        return "exchange = $1", [scope_ref]
    if scope in (SafetyScope.TENANT, SafetyScope.ACCOUNT):
        try:
            user_id = UUID(scope_ref)
        except ValueError as exc:
            raise MalformedScopeRefError(
                f"{scope.value} scope_ref는 users.user_id(UUID)여야 합니다: {scope_ref!r}"
            ) from exc
        return "user_id = $1", [user_id]
    if scope == SafetyScope.STRATEGY_DEPLOYMENT:
        if scope_ref.startswith("exec:"):
            raw_id = scope_ref.removeprefix("exec:")
            if not raw_id.isdigit():
                raise MalformedScopeRefError(
                    f"STRATEGY_DEPLOYMENT scope_ref 'exec:<int>' 형식이 아닙니다: {scope_ref!r}"
                )
            return "id = $1", [int(raw_id)]
        if scope_ref.startswith("dep:"):
            return None, []
        raise MalformedScopeRefError(
            "STRATEGY_DEPLOYMENT scope_ref는 'exec:<int>' 또는 'dep:<uuid>'여야 합니다: "
            f"{scope_ref!r}"
        )
    raise UnmappedSafetyScopeError(f"§3.8 매핑 표에 없는 SafetyScope: {scope!r}")
