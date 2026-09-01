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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.event_bus.in_process import InProcessEventBus
from src.core.loader.secret_loader import load_env_secrets
from src.core.logging.audit_log import record_audit_log
from src.core.notifications.gateway import NotificationGateway
from src.core.safety.heartbeat import DEFAULT_HEARTBEAT_PATH, write_heartbeat
from src.services.credential_resolver import CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService

HEARTBEAT_INTERVAL_SECONDS = 2.0  # Draft — watchdog_process.py의 5초 폴링 주기보다 짧게


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

    app.state.pool = pool
    app.state.secrets = secrets
    app.state.event_bus = event_bus
    app.state.credential_resolver = credential_resolver
    try:
        yield
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
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
    app.include_router(executions.router, prefix="/executions", tags=["executions"])
    app.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
    app.include_router(admin.router, tags=["admin"])
    app.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"])
    app.include_router(reports.router, prefix="/reports", tags=["reports"])
    app.include_router(
        device_tokens.router, prefix="/device-tokens", tags=["device-tokens"]
    )
    app.include_router(wallet.router, prefix="/wallet", tags=["wallet"])

    return app


app = create_app()
