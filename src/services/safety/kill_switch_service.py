"""KillSwitchService — 5범위(GLOBAL/TENANT/ACCOUNT/PROVIDER/STRATEGY_DEPLOYMENT)
kill switch의 단일 권위 진입점.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2 표 136행(R-40), §4.3
상태표 412~413행, §5 표 439~458행(트랜잭션 경계), §9 R-40(선행 R-38
`legacy_execution_pauser.py`, R-39 `open_order_sweeper.py`).

I3(§8 392행) — "Kill switch 권위는 safety_control+safety_fence뿐 ...
KillSwitchService가 유일한 writer, CI grep으로 INSERT INTO safety_control
호출부가 정확히 1곳임을 강제". 이 서비스는 그 INSERT를 직접 하지 않는다 —
여전히 `postgres_repository.py`의 `insert_safety_control()` 한 곳뿐이고,
이 서비스는 `activate_safety_control()`(응용 계층)을 통해서만 그 경로를
탄다. "유일한 writer"라는 말은 "이 서비스가 유일하게 허용된 진입점"이라는
뜻이지 SQL을 두 번째로 여기 복제한다는 뜻이 아니다 — 복제하면 오히려 grep
불변조건이 깨진다.

§4.3 표 412행이 명시하는 activate의 부작용 순서를 그대로 따른다: control
생성(fence++, 감사) → legacy 실행 정지 → paper_control fan-out →
open_order_sweeper → 알림. 앞 단계(제어 생성)는 `activate_safety_control()`
안에서 커밋되고, 뒤 세 단계는 `on_activated` 훅으로 그 트랜잭션 밖에서
각자 독립적으로 실행된다(§5 "트랜잭션 경계" 문단) — 응용 계층이 커넥션을
쥔 채 두 번째 커넥션을 얻지 않는다(§2 P1 교착 패턴 금지)는 규칙을 지키기
위해서다.

fan-out 실패 처리: §5 표 448행은 실패를 `risk_signal`(PROVIDER_OUTAGE)로
기록하고 재시도하라고 하지만, `risk_signal` 테이블은 아직 없다(R-46
마이그레이션 `d6f7b4c3e5a6`, 이 리프 스콥 밖 — 새 마이그레이션을 만들지
말라는 이 리프의 decision과도 맞다). 그때까지는 구조화 로그
(`logger.exception`)로만 남긴다 — 미검증 상태를 성공으로 위장하지 않되,
control 자체(권위 있는 차단)는 이미 커밋돼 있으므로 fan-out 개별 실패가
전체 activate() 호출을 실패시키지는 않는다(레드팀 관점: kill switch의
1차 방어선은 safety_control+fence이고, legacy pause/paper_control/sweeper는
정리(clean-up) 계층이다 — R-38/R-39가 이미 채택한 원칙과 동일).

"알림"(§4.3 412행 마지막 부작용)도 같은 이유로 아직 실제 게이트웨이가
없다(`risk_alerting.py`는 R-55, 미착수) — 이 리프에서는 구조화 로그로
대체하고, R-55가 생기면 그 지점에서 실제 채널로 바꾸면 된다.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import UUID

import asyncpg

from src.core.observability.context import bind
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.contracts.v1 import Classification as AuditClassification
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.paper_control.application.apply_safety_control import (
    apply_safety_control_to_deployments,
)
from src.foundation.paper_control.ports.repository import PaperControlRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.application.deactivate_safety_control import (
    deactivate_safety_control,
)
from src.foundation.risk_gate.contracts.v1 import SafetyControlView
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.safety.legacy_execution_pauser import pause_executions_for_scope
from src.services.safety.open_order_sweeper import sweep_open_orders

logger = logging.getLogger(__name__)


class MissingEvidenceRefError(Exception):
    """§4.3 413행 guard "evidence_ref 필수" — 없으면 해제 자체를 거부한다
    (recovery review의 근거 없이 kill switch를 끄는 것을 막는 fail-closed
    지점)."""


class KillSwitchService:
    """`activate`/`deactivate` 둘 다 이 클래스를 거쳐야 한다 — 라우터·워커
    (circuit breaker 재악화, liquidation watchdog 등)가 각자
    `activate_safety_control()`을 직접 호출하며 fan-out을 매번 다르게
    빠뜨리는 것(현재 라우터가 `open_order_sweeper`를 전혀 호출하지 않는
    상태가 그 증거)을 막기 위한 단일 진입점이다. 기존 라우터/reconciliation
    호출부를 이 서비스로 바꿔 넣는 배선은 이 리프 스콥이 아니다(R-40
    decision — R-41이 `risk_guard_service.py`를 이 서비스로 통일하는
    다음 리프)."""

    def __init__(
        self,
        *,
        risk_gate_repo: RiskGateRepository,
        pg_pool: asyncpg.Pool,
        paper_control_repo: PaperControlRepository,
        exchange_adapters: Mapping[str, ExchangeAdapter],
        audit_repo: AuditEventRepository | None = None,
    ) -> None:
        self._risk_gate_repo = risk_gate_repo
        self._pg_pool = pg_pool
        self._paper_control_repo = paper_control_repo
        self._exchange_adapters = exchange_adapters
        self._audit_repo = audit_repo

    async def activate(
        self,
        *,
        scope: SafetyScope,
        scope_ref: str | None,
        reason: str,
        actor_subject_id: UUID,
        actor_is_admin: bool,
        trace_id: UUID,
    ) -> SafetyControlView:
        """`tenant_id`를 별도로 받지 않는다 — 이 코드베이스의 모든 기존
        호출부(라우터·테스트)가 항상 `tenant_id == actor_subject_id`로
        호출한다(`activate_safety_control()`의 `tenant_id`는 "행위자가
        속한 tenant"일 뿐 조회 대상이 아니다 — 대상은 `scope_ref`).
        """

        async def _on_activated(view: SafetyControlView) -> None:
            await self._fan_out(view, actor_subject_id=actor_subject_id, trace_id=trace_id)

        return await activate_safety_control(
            self._risk_gate_repo,
            tenant_id=actor_subject_id,
            actor_subject_id=actor_subject_id,
            actor_is_admin=actor_is_admin,
            scope=scope,
            scope_ref=scope_ref,
            reason=reason,
            trace_id=trace_id,
            audit_repo=self._audit_repo,
            on_activated=_on_activated,
        )

    async def _fan_out(
        self, view: SafetyControlView, *, actor_subject_id: UUID, trace_id: UUID
    ) -> None:
        scope = SafetyScope(view.scope.value)

        try:
            async with self._pg_pool.acquire() as conn:
                paused_ids = await pause_executions_for_scope(
                    conn, scope, view.scope_ref, control_id=view.id
                )
            logger.info(
                "KillSwitchService.activate(control=%s, trace=%s): legacy 정지 %d건",
                view.id,
                trace_id,
                len(paused_ids),
            )
        except Exception:  # noqa: BLE001 — clean-up 단계 실패가 kill switch 자체를 막지 않는다
            logger.exception(
                "KillSwitchService.activate(control=%s, trace=%s): legacy 정지 실패",
                view.id,
                trace_id,
            )

        try:
            paused_deployments = await apply_safety_control_to_deployments(
                self._paper_control_repo,
                scope=scope,
                scope_ref=view.scope_ref,
                safety_control_id=view.id,
                actor_subject_id=actor_subject_id,
                reason=view.reason,
            )
            logger.info(
                "KillSwitchService.activate(control=%s, trace=%s): paper_control 정지 %d건",
                view.id,
                trace_id,
                len(paused_deployments),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "KillSwitchService.activate(control=%s, trace=%s): paper_control fan-out 실패",
                view.id,
                trace_id,
            )

        try:
            report = await sweep_open_orders(
                self._pg_pool,
                self._exchange_adapters,
                control_id=view.id,
                scope=scope,
                scope_ref=view.scope_ref,
            )
            logger.info(
                "KillSwitchService.activate(control=%s, trace=%s): 미체결 정리 "
                "요청 %d건, 실패 %d건",
                view.id,
                trace_id,
                len(report.cancel_requested),
                len(report.adapter_failed),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "KillSwitchService.activate(control=%s, trace=%s): open_order_sweeper 실패",
                view.id,
                trace_id,
            )

        # 알림(§4.3 412행) — risk_alerting.py(R-55)가 아직 없어 구조화 로그로
        # 대체한다. WARNING: 운영자가 반드시 인지해야 하는 이벤트라 INFO가
        # 아니다.
        logger.warning(
            "KillSwitchService: safety_control %s ACTIVE(scope=%s, scope_ref=%s, "
            "reason=%r, trace=%s) — fan-out 완료",
            view.id,
            scope.value,
            view.scope_ref,
            view.reason,
            trace_id,
        )

    async def deactivate(
        self,
        control_id: UUID,
        *,
        evidence_ref: str,
        actor_subject_id: UUID,
        actor_is_admin: bool,
        trace_id: UUID,
    ) -> SafetyControlView:
        """§4.3 413행 — "아무것도 재개하지 않는다": 이 메서드는 control을
        INACTIVE로 표시할 뿐, legacy 실행/paper_control을 재개시키지 않는다
        (그 경로는 RECOVERY 게이트를 통과해야 하는 별도 `start`/`resume`
        커맨드의 책임 — 414행)."""
        if not evidence_ref:
            raise MissingEvidenceRefError(
                f"control {control_id} 해제에는 evidence_ref가 필요합니다."
            )

        with bind(trace_id=trace_id):
            view = await deactivate_safety_control(
                self._risk_gate_repo,
                tenant_id=actor_subject_id,
                actor_is_admin=actor_is_admin,
                control_id=control_id,
                audit_repo=self._audit_repo,
            )

            if self._audit_repo is not None:
                scope = SafetyScope(view.scope.value)
                event_tenant_id = (
                    UUID(view.scope_ref)
                    if scope in (SafetyScope.TENANT, SafetyScope.ACCOUNT)
                    else None
                )
                await record_command_event(
                    self._audit_repo,
                    tenant_id=event_tenant_id,
                    aggregate_type="safety_control",
                    aggregate_id=control_id,
                    action="safety_control_deactivation_evidence_recorded",
                    actor_subject_id=actor_subject_id,
                    classification=AuditClassification.CONFIDENTIAL,
                    payload={"evidence_ref": evidence_ref},
                )

        return view
