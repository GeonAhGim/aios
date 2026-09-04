"""`RiskDecisionRecorder` — R-25 결정 영속화 + 감사 + 이벤트.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.5 102행, §9 R-25, §6 실패모드
"시계 드리프트", §5 쓰기 표(`risk_decision`은 append-only·PK 충돌은 재시도
없이 전파, 이벤트는 커밋 후 in-process bus 발행·유실 허용), §7 로그 필드
(잔고 원값·`inputs_snapshot` 전문 금지, hash만).

WORM 삽입 자체는 R-24 `PostgresDecisionRepository.insert()`만 쓴다 — 이
모듈은 시계 드리프트 보정·`audit_log` 기록·이벤트 발행만 맡는다.

105번 §2 P1 교착 금지: 커넥션을 쥔 채로 `decision_repo.insert()`(자기
커넥션을 따로 얻는다)를 부르지 않는다 — 매번 풀에서 새로 빌리고 반납한다.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Protocol

import asyncpg

from src.core.event_bus.bus import EventBus
from src.core.logging.audit_log import record_audit_log
from src.core.risk.decision import RiskDecision, RiskOutcome
from src.core.risk.inputs import RiskInputs

logger = logging.getLogger(__name__)

TOPIC_DECISION_RECORDED = "risk.decision.recorded"
TOPIC_LIMIT_BREACHED = "risk.limit.breached"

_CLOCK_SKEW_TOLERANCE = timedelta(seconds=2)
_REASON_CLOCK_SKEW = "RISK_INPUT_STALE"
_LIMIT_BREACH_PREFIX = "RISK_LIMIT_BREACH:"


class DecisionRepository(Protocol):
    async def insert(self, decision: RiskDecision, inputs_snapshot: dict[str, Any]) -> None: ...


def _decision_audit_summary(decision: RiskDecision) -> dict[str, Any]:
    """§7 금지 목록(잔고 원값·inputs_snapshot 전문) 회피 — 메타데이터·해시만."""
    return {
        "decision_id": str(decision.decision_id),
        "gate_kind": decision.gate_kind.value,
        "outcome": decision.outcome.value,
        "reason_codes": list(decision.reason_codes),
        "obligations": list(decision.obligations),
        "rule_version": decision.rule_version,
        "rule_hash": decision.rule_hash,
        "engine_version": decision.engine_version,
        "inputs_hash": decision.inputs_hash,
    }


def _decision_event_payload(decision: RiskDecision) -> dict[str, Any]:
    return {
        "event_type": TOPIC_DECISION_RECORDED,
        "decision_id": str(decision.decision_id),
        "tenant_id": str(decision.tenant_id),
        "execution_ref": decision.execution_ref,
        "gate_kind": decision.gate_kind.value,
        "outcome": decision.outcome.value,
        "reason_codes": list(decision.reason_codes),
        "rule_hash": decision.rule_hash,
        "trace_id": str(decision.trace_id),
    }


def _limit_breach_payloads(decision: RiskDecision) -> list[dict[str, Any]]:
    """§3.4 `RISK_LIMIT_BREACH:<scope>:<metric>` reason_code마다 하나씩."""
    payloads: list[dict[str, Any]] = []
    for reason_code in decision.reason_codes:
        if not reason_code.startswith(_LIMIT_BREACH_PREFIX):
            continue
        _, scope, metric = reason_code.split(":", 2)
        payloads.append(
            {
                "event_type": TOPIC_LIMIT_BREACHED,
                "decision_id": str(decision.decision_id),
                "tenant_id": str(decision.tenant_id),
                "reason_code": reason_code,
                "scope": scope,
                "metric": metric,
            }
        )
    return payloads


class RiskDecisionRecorder:
    def __init__(
        self, pool: asyncpg.Pool, decision_repo: DecisionRepository, event_bus: EventBus
    ) -> None:
        self._pool = pool
        self._decision_repo = decision_repo
        self._event_bus = event_bus

    async def record(self, decision: RiskDecision, inputs: RiskInputs, *, actor: str) -> None:
        effective = await self._apply_clock_skew_guard(decision)
        inputs_snapshot = inputs.model_dump(mode="json")  # 재해시 안 함 — mode="python" 불필요

        # append-only WORM — PK 충돌(decision_id 재사용)은 호출자 버그이니 재시도
        # 없이 전파한다. 이 줄이 실패하면 아래 audit_log·이벤트는 실행되지 않는다.
        await self._decision_repo.insert(effective, inputs_snapshot)

        async with self._pool.acquire() as conn:
            await record_audit_log(
                conn,
                actor_agent=actor,
                action_type="risk_decision_recorded",
                decision_data=_decision_audit_summary(effective),
                user_id=effective.tenant_id,
                target_type="risk_decision",
                target_id=str(effective.decision_id),
                trace_id=effective.trace_id,
            )

        await self._event_bus.publish(TOPIC_DECISION_RECORDED, _decision_event_payload(effective))
        for payload in _limit_breach_payloads(effective):
            await self._event_bus.publish(TOPIC_LIMIT_BREACHED, payload)

    async def _apply_clock_skew_guard(self, decision: RiskDecision) -> RiskDecision:
        """§6 시계 드리프트 — WORM이라 사후 정정이 불가능하므로 저장 *전에*
        `evaluated_at`·DB `now()` 차를 확인해 2초 초과면 DENY로 고정한다."""
        async with self._pool.acquire() as conn:
            db_now = await conn.fetchval("SELECT now()")
        drift = abs(db_now - decision.evaluated_at)
        if drift <= _CLOCK_SKEW_TOLERANCE:
            return decision

        logger.warning(
            "clock_skew_detected decision_id=%s drift_seconds=%.3f",
            decision.decision_id,
            drift.total_seconds(),
        )
        reason_codes = decision.reason_codes
        if _REASON_CLOCK_SKEW not in reason_codes:
            reason_codes = (*reason_codes, _REASON_CLOCK_SKEW)
        return decision.model_copy(
            update={"outcome": RiskOutcome.DENY, "reason_codes": reason_codes, "obligations": ()}
        )


__all__ = [
    "RiskDecisionRecorder",
    "DecisionRepository",
    "TOPIC_DECISION_RECORDED",
    "TOPIC_LIMIT_BREACHED",
]
