"""16번대 — 라우터 등록표(`src/main.py`에서 분리).

`create_app()`이 아키텍처 가드 P6.line_cap(300줄, meta/guards/common.py)에
닿아(task-1377 LB-19 라우터를 배선할 자리가 없었다) `include_router` 목록만
이 모듈로 옮겼다 — 등록 순서·prefix·tags는 옮기기 전 `src/main.py`와
바이트 단위로 같다(동작 변경 없음). 라우터 모듈 import를 함수 안에 두는
이유도 그대로다: 라우터들이 `src.api.deps`→설정 로더를 끌어오므로 앱
조립 시점(lifespan/미들웨어 구성 이후)에만 읽는다.

새 라우터는 `src/main.py`가 아니라 여기에 한 줄 추가한다.
"""
from __future__ import annotations

from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    from src.api.routers import (
        admin,
        alerts,
        auth,
        device_tokens,
        exchange_credentials,
        executions,
        health,
        market_data,
        marketplace,
        metrics,
        notifications,
        portfolio,
        positions,
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
        exchange_credentials.router, prefix="/exchange-credentials", tags=["exchanges"])
    app.include_router(marketplace.router, prefix="/marketplace", tags=["marketplace"])
    app.include_router(
        strategy_builder.router, prefix="/strategy-builder", tags=["strategy-builder"])
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
    app.include_router(market_data.router)  # LA-24(task-1376) /v1/foundation/market-data
    app.include_router(positions.router)
    app.include_router(executions.router, prefix="/executions", tags=["executions"])
    app.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
    app.include_router(admin.router, tags=["admin"])
    app.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"])
    app.include_router(reports.router, prefix="/reports", tags=["reports"])
    app.include_router(device_tokens.router, prefix="/device-tokens", tags=["device-tokens"])
    app.include_router(wallet.router, prefix="/wallet", tags=["wallet"])
    app.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
    app.include_router(metrics.router)
    app.include_router(health.router)
