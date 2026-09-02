"""16번대 — FastAPI 앱 조립.

Spec: 16_backend_signatures.md, ADR-2026-08-10-B

편차: ADR-2026-08-10-B/16_backend_signatures.md 초기 Draft는 SQLAlchemy
AsyncSession 기반 get_db()를 가정했지만, 이 세션에서 실제로 만든 40여개
서비스는 전부 asyncpg.Pool을 직접 받는 방식으로 구현됐다 — raw asyncpg가
이미 모든 서비스의 실제 계약이라 지금 SQLAlchemy로 바꾸면 그 서비스들을
전부 다시 써야 하는데 이득이 없다. 이 조립 단계에서는 SQLAlchemy를 쓰지
않고 asyncpg.Pool 하나를 app.state에 두고 각 라우터가 Depends로 꺼내
쓰는 방식으로 확정한다.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.event_bus.in_process import InProcessEventBus
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.loader.secret_loader import load_env_secrets
from src.core.logging.audit_log import record_audit_log
from src.core.notifications.gateway import NotificationGateway
from src.core.safety.heartbeat import DEFAULT_HEARTBEAT_PATH, write_heartbeat
from src.services.alert_service import AlertService
from src.services.credential_resolver import CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService
from src.services.execution_service import ExecutionService
from src.services.risk_guard_service import RiskGuardService

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 2.0  # Draft — watchdog_process.py의 5초 폴링 주기보다 짧게
ALERT_EVALUATION_INTERVAL_SECONDS = 60.0  # Draft — 가격/지표 알림 평가 주기
RISK_GUARD_INTERVAL_SECONDS = 30.0  # Draft — 손실 한도 자동정지 평가 주기


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    secrets = load_env_secrets()
    pool = await asyncpg.create_pool(_asyncpg_dsn(secrets.database_url.get_secret_value()))

    async def _event_bus_audit_sink(record: dict[str, Any]) -> None:
        """§5.5가 요구하는 "모든 handler 예외는 audit_log에 자동 기록"을 실제
        audit_log 테이블(7.4)에 연결한다 — InProcessEventBus 기본값은 로거
        대체 기록뿐이라 이 콜백 없이는 EventBus 자체의 실패가 감사 이력에
        남지 않는다."""
        async with pool.acquire() as conn:
            await record_audit_log(
                conn,
                actor_agent=record["actor_agent"],
                action_type=record["action_type"],
                target_type=record.get("target_type"),
                target_id=record.get("target_id"),
                decision_data=record["decision_data"],
            )

    # FD-17.1 — 실제 이메일/푸시 발송기(SMTP·FCM/APNs)는 아직 미확정(Draft)이라
    # senders 없이 등록한다. "발송 실패"로 정직하게 기록되고(§17.1 원칙 —
    # 발송됐는지 확인 못 하는 상태를 성공으로 위장하지 않는다) EventBus의
    # CRITICAL 재시도(최대 5회, §5.5)를 거쳐 audit_log에 남는다. 실제 발송기가
    # 정해지면 NotificationGateway(pool, senders={...})로 교체하기만 하면 된다.
    event_bus = InProcessEventBus(audit_sink=_event_bus_audit_sink)
    NotificationGateway(pool).register(event_bus)
    await event_bus.start()

    async def _heartbeat_loop() -> None:
        """FD-9.1 — watchdog_process.py(별도 OS 프로세스)가 이 메인 프로세스의
        생사를 판정하는 유일한 신호. 프로세스 메모리를 공유하지 않으므로
        파일 타임스탬프로만 통신한다(core/safety/heartbeat.py)."""
        while True:
            write_heartbeat(DEFAULT_HEARTBEAT_PATH)
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    # 레드팀 감사(docs/RED_TEAM_FINDINGS.md #02) 반영 — CredentialResolver는
    # 5분 TTL로 어댑터를 캐싱하도록 설계됐는데, get_credential_resolver()가
    # 매 요청마다 새 인스턴스를 만들면 내부 _cache가 매번 빈 채로 시작해
    # 캐시가 한 번도 실제로 작동한 적이 없었다 — pool/event_bus와 동일하게
    # 앱 시작 시 한 번만 만들어 app.state에 둔다.
    credential_service = ExchangeCredentialService(
        pool, encryption_key=secrets.credential_encryption_key.get_secret_value()
    )
    credential_resolver = CredentialResolver(credential_service)

    # FD-14(신설) — 가격/지표 알림 평가 루프. heartbeat_loop과 동일 패턴
    # (main.py가 유일한 백그라운드 스케줄러 지점) — 알림 하나 평가가
    # 실패해도(자격증명 해지 등) 다음 알림·다음 주기로 계속 진행한다
    # (alert_service.py::evaluate_all_active 참조).
    alert_service = AlertService(
        pool, credential_resolver=credential_resolver, publish=event_bus.publish
    )

    async def _alert_evaluation_loop() -> None:
        while True:
            await asyncio.sleep(ALERT_EVALUATION_INTERVAL_SECONDS)
            # 레드팀 #2026-09-02-21 — evaluate_all_active()는 개별 알림 실패를
            # 내부에서 이미 건너뛰지만, 이 호출 자체(또는 그 안에서 예상 못한
            # 예외)가 이 루프를 빠져나가면 alert_task 코루틴이 영구히 죽어
            # 재시작 전까지 아무 사용자의 알림도 평가되지 않는다 — 두 번째
            # 방어선으로 여기서도 잡아 다음 주기에 계속 시도한다.
            try:
                await alert_service.evaluate_all_active()
            except Exception:
                logger.exception(
                    "alert_evaluation_loop: 이번 주기 평가 실패 — 다음 주기에 재시도합니다."
                )

    alert_task = asyncio.create_task(_alert_evaluation_loop())

    # ZuluTrade식 "위험 관리" — 실행별 손실 한도(%) 자동 정지 루프. 위 두
    # 루프와 동일 패턴(main.py가 유일한 백그라운드 스케줄러 지점).
    risk_guard_service = RiskGuardService(
        pool,
        ExecutionService(pool, load_risk_policy(), publish=event_bus.publish),
        publish=event_bus.publish,
    )

    async def _risk_guard_loop() -> None:
        while True:
            await asyncio.sleep(RISK_GUARD_INTERVAL_SECONDS)
            await risk_guard_service.evaluate_all_running()

    risk_guard_task = asyncio.create_task(_risk_guard_loop())

    app.state.pool = pool
    app.state.secrets = secrets
    app.state.event_bus = event_bus
    app.state.credential_resolver = credential_resolver
    try:
        yield
    finally:
        heartbeat_task.cancel()
        alert_task.cancel()
        risk_guard_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        with contextlib.suppress(asyncio.CancelledError):
            await alert_task
        with contextlib.suppress(asyncio.CancelledError):
            await risk_guard_task
        await event_bus.stop()
        await pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="AIOS API", lifespan=lifespan)

    # FD-17 프론트엔드(apps/web, 기본 Vite 포트 5173)가 별도 오리진에서 API를
    # 호출하므로 CORS 허용이 필요 — .env CORS_ALLOWED_ORIGINS는 정의만 되어
    # 있었고 실제로 적용된 적이 없었다(프론트엔드가 없어 아무도 마주친 적 없는
    # 상태였음). lifespan과 별개로 미들웨어는 앱 생성 시점에 등록해야 해서
    # secrets를 여기서 한 번 더 읽는다.
    secrets = load_env_secrets()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=secrets.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from src.api.routers import (
        admin,
        alerts,
        auth,
        device_tokens,
        exchange_credentials,
        executions,
        marketplace,
        notifications,
        portfolio,
        reports,
        strategy_builder,
        suitability,
        users,
        wallet,
    )
    from src.api.routers.foundation import connections as foundation_connections
    from src.api.routers.foundation import evidence as foundation_evidence
    from src.api.routers.foundation import mandates as foundation_mandates
    from src.api.routers.foundation import paper_control as foundation_paper_control
    from src.api.routers.foundation import reconciliation as foundation_reconciliation
    from src.api.routers.foundation import risk_gate as foundation_risk_gate
    from src.api.routers.foundation import trust as foundation_trust
    from src.api.routers.foundation import validation as foundation_validation

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(users.router, prefix="/users", tags=["users"])
    app.include_router(
        exchange_credentials.router, prefix="/exchange-credentials", tags=["exchanges"]
    )
    app.include_router(marketplace.router, prefix="/marketplace", tags=["marketplace"])
    app.include_router(
        strategy_builder.router, prefix="/strategy-builder", tags=["strategy-builder"]
    )
    app.include_router(suitability.router)
    app.include_router(foundation_trust.router)
    app.include_router(foundation_mandates.router)
    app.include_router(foundation_evidence.router)
    app.include_router(foundation_connections.router)
    app.include_router(foundation_validation.router)
    app.include_router(foundation_risk_gate.router)
    app.include_router(foundation_paper_control.router)
    app.include_router(foundation_reconciliation.router)
    app.include_router(executions.router, prefix="/executions", tags=["executions"])
    app.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
    app.include_router(admin.router, tags=["admin"])
    app.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"])
    app.include_router(reports.router, prefix="/reports", tags=["reports"])
    app.include_router(
        device_tokens.router, prefix="/device-tokens", tags=["device-tokens"]
    )
    app.include_router(wallet.router, prefix="/wallet", tags=["wallet"])
    app.include_router(alerts.router, prefix="/alerts", tags=["alerts"])

    return app


app = create_app()
