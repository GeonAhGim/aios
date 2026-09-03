"""16번대 — FastAPI 앱 조립.

Spec: 16_backend_signatures.md, ADR-2026-08-10-B

편차: 초기 Draft는 SQLAlchemy AsyncSession 기반 get_db()를 가정했지만, 이미
만들어진 40여개 서비스가 전부 asyncpg.Pool을 직접 받는 방식이라 raw asyncpg가
실제 계약이다 — asyncpg.Pool 하나를 app.state에 두고 라우터가 Depends로 꺼낸다.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.contracts.handlers import install_exception_handlers
from src.api.middleware.request_id import RequestIdMiddleware
from src.core.event_bus.in_process import InProcessEventBus
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.loader.secret_loader import load_env_secrets
from src.core.logging.audit_log import record_audit_log
from src.core.logging.schema import configure_logging
from src.core.notifications.gateway import NotificationGateway
from src.core.observability.metrics_registry import get_registry
from src.core.safety.metrics_collector import ApiCallTracker
from src.exchanges.common.instrumented_adapter import instrumented_adapter_factory
from src.exchanges.factory import build_adapter
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.adapters.postgres_payout_repository import PostgresPayoutRepository
from src.foundation.ledger.application.scheduler import LedgerIntegrityScheduler
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.scheduler import MarketDataQualityScheduler
from src.services.background_loops import flag_enabled, start_background_loops
from src.services.credential_resolver import CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService

logger = logging.getLogger(__name__)


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 07번 §7.1 — JSON Lines 구조화 로깅. 스키마는 있었으나 호출자가 없어
    # 운영에서 한 번도 활성화되지 않았다(전수감사 §3).
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    secrets = load_env_secrets()
    policy = load_risk_policy()
    pool = await asyncpg.create_pool(_asyncpg_dsn(secrets.database_url.get_secret_value()))

    async def _event_bus_audit_sink(record: dict[str, Any]) -> None:
        """§5.5 "모든 handler 예외는 audit_log에 자동 기록"을 실제 audit_log
        테이블(7.4)에 연결 — 이 콜백 없이는 EventBus 실패가 기록되지 않는다."""
        async with pool.acquire() as conn:
            await record_audit_log(
                conn,
                actor_agent=record["actor_agent"],
                action_type=record["action_type"],
                target_type=record.get("target_type"),
                target_id=record.get("target_id"),
                decision_data=record["decision_data"],
            )

    # FD-17.1 — 실제 이메일/푸시 발송기(SMTP·FCM/APNs)는 미확정(Draft)이라
    # senders 없이 등록한다. "발송 실패"로 정직하게 기록되며(§17.1 — 성공
    # 위장 금지) EventBus CRITICAL 재시도(§5.5)를 거쳐 audit_log에 남는다.
    event_bus = InProcessEventBus(audit_sink=_event_bus_audit_sink)
    NotificationGateway(pool).register(event_bus)
    await event_bus.start()

    # 레드팀 감사(#02) — CredentialResolver는 5분 TTL 캐싱을 설계했지만
    # 요청마다 새로 만들면 _cache가 매번 비어 시작해 캐시가 작동한 적이
    # 없었다 — pool/event_bus와 동일하게 시작 시 한 번만 만들어 둔다.
    credential_service = ExchangeCredentialService(
        pool, encryption_key=secrets.credential_encryption_key.get_secret_value()
    )
    # PM 배정 ⑤ 2단계 — CircuitBreakerMetrics가 소비할 어댑터 호출 성공/실패를
    # InstrumentedAdapter에서만 계측한다. background_loops의 safety 루프와 공유.
    api_tracker = ApiCallTracker()
    credential_resolver = CredentialResolver(
        credential_service,
        adapter_factory=instrumented_adapter_factory(api_tracker, build_adapter),
    )

    # FD-8/FD-9/FD-14 — heartbeat/alert/risk_guard/execution_loop/safety
    # 재가동 루프 생성·재시작 복구는 src/services/background_loops.py로 분리했다
    # (P6 — main.py 300줄 초과 금지). lifespan은 시작·정지만 담당한다.
    loops = await start_background_loops(
        pool=pool,
        policy=policy,
        event_bus=event_bus,
        credential_resolver=credential_resolver,
        api_tracker=api_tracker,
    )

    # LC-10/LC-16 — 원장 무결성 검증(5분 주기)·정산 배치(일 1회 00:10 KST)
    # 백그라운드 루프. LC-10에서는 스케줄러 클래스만 만들고 이 배선을 후속
    # 리프로 남겼다(그 모듈 docstring 참조) — 위 `loops`(execution_loop 등)와
    # 같은 패턴(한 주기 실패가 루프를 죽이지 않음)이라 별도 플래그로 끌 수
    # 있게 한다(테스트가 lifespan을 통째로 띄울 때 공유 DB에 원치 않는
    # write_frozen을 세우지 않도록, `AIOS_EXECUTION_LOOP_ENABLED`와 동일 관례).
    ledger_scheduler = LedgerIntegrityScheduler(
        pool,
        journal=PostgresJournalRepository(pool),
        balances=PostgresBalanceRepository(pool),
        audit=PostgresAuditEventRepository(pool),
        registry=get_registry(),
        payouts=PostgresPayoutRepository(pool),
    )
    ledger_tasks: list[asyncio.Task[None]] = []
    if flag_enabled("AIOS_LEDGER_SCHEDULER_ENABLED"):
        ledger_tasks = [
            asyncio.create_task(ledger_scheduler.run_forever()),
            asyncio.create_task(ledger_scheduler.run_payout_forever()),
        ]
    else:
        logger.warning(
            "ledger_scheduler: AIOS_LEDGER_SCHEDULER_ENABLED=0 — 원장 스케줄러를 띄우지 않습니다."
        )

    # LA-18 — 시장데이터 품질 게이지(스테일·갭·거부 비율) 주기 export. 위
    # ledger_scheduler와 같은 패턴·같은 플래그 관례. `watched`(주기 재수집
    # 대상)는 아직 운영 심볼 목록·자격증명 배선이 없어(§10 미확정, 이
    # 리프 범위 밖) 비워 둔다 — 이 스케줄러는 지금은 이미 저장된 배치를
    # 훑어 게이지만 갱신한다(quality_metrics.py 모듈 docstring 참조).
    market_data_scheduler = MarketDataQualityScheduler(
        pool,
        store=PostgresCandleStore(pool),
        refs=PostgresReferenceRepository(pool),
        cal=PostgresCalendarRepository(pool),
        batches=PostgresBatchRepository(pool),
        registry=get_registry(),
    )
    market_data_tasks: list[asyncio.Task[None]] = []
    if flag_enabled("AIOS_MARKET_DATA_SCHEDULER_ENABLED"):
        market_data_tasks = [asyncio.create_task(market_data_scheduler.run_forever())]
    else:
        logger.warning(
            "market_data_scheduler: AIOS_MARKET_DATA_SCHEDULER_ENABLED=0 — "
            "시장데이터 품질 스케줄러를 띄우지 않습니다."
        )

    app.state.pool = pool
    app.state.secrets = secrets
    app.state.event_bus = event_bus
    app.state.credential_resolver = credential_resolver
    app.state.execution_scheduler = loops.execution_scheduler
    app.state.ledger_scheduler = ledger_scheduler
    app.state.market_data_scheduler = market_data_scheduler
    try:
        yield
    finally:
        for task in [*ledger_tasks, *market_data_tasks]:
            task.cancel()
        for task in [*ledger_tasks, *market_data_tasks]:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await loops.stop()
        await event_bus.stop()
        await pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="AIOS API", lifespan=lifespan)

    # L4 §2.3(C) — 전역 예외 핸들러. 레거시 라우터 대부분이 자체적으로 예외를
    # 잡아 HTTPException으로 변환하므로 아직 개입은 적지만(규칙 안전망 —
    # 새어나간 도메인 예외를 봉투 형식으로 응답), §9 PLT-2x 이관이 늘수록 커진다.
    install_exception_handlers(app)

    # FD-17 프론트엔드(apps/web, Vite 5173)가 별도 오리진이라 CORS가 필요 —
    # 미들웨어는 앱 생성 시점에 등록해야 해서 secrets를 여기서 한 번 더 읽는다.
    secrets = load_env_secrets()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=secrets.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # request_id 미들웨어는 CORS보다 나중에 등록해 요청 스택 가장 바깥을 감싼다.
    app.add_middleware(RequestIdMiddleware)

    from src.api.routers import (
        admin,
        alerts,
        auth,
        device_tokens,
        exchange_credentials,
        executions,
        marketplace,
        metrics,
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
    from src.api.routers.foundation import ledger_admin as foundation_ledger_admin
    from src.api.routers.foundation import mandates as foundation_mandates
    from src.api.routers.foundation import paper_control as foundation_paper_control
    from src.api.routers.foundation import performance as foundation_performance
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
    app.include_router(foundation_ledger_admin.router)
    app.include_router(foundation_connections.router)
    app.include_router(foundation_validation.router)
    app.include_router(foundation_risk_gate.router)
    app.include_router(foundation_paper_control.router)
    app.include_router(foundation_reconciliation.router)
    app.include_router(foundation_performance.router)
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
    app.include_router(metrics.router)

    return app


app = create_app()
