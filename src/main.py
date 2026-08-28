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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from src.core.loader.secret_loader import load_env_secrets


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    secrets = load_env_secrets()
    pool = await asyncpg.create_pool(_asyncpg_dsn(secrets.database_url))
    app.state.pool = pool
    app.state.secrets = secrets
    try:
        yield
    finally:
        await pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="AIOS API", lifespan=lifespan)

    from src.api.routers import (
        admin,
        auth,
        exchange_credentials,
        executions,
        marketplace,
        notifications,
        portfolio,
        reports,
        strategy_builder,
        suitability,
        users,
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

    return app


app = create_app()
