"""PLT-09 — 헬스체크 엔드포인트: `/readyz`(readiness) · `/livez`(liveness).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.2, §9 PLT-09.

`ReadinessReport`/`CheckResult`는 frontend/packages/api-client/src/readiness.ts가
이미 소비 중인 계약(task-466)을 SSOT로 그대로 따른다 — 필드명을 새로 짓지
않는다. `/readyz`·`/livez`는 §3.3 `ApiResponse` 봉투를 쓰지 않는 예외(운영
프로브)라 라우터가 직접 `JSONResponse`를 만든다.

`checks`는 지금 `LoopHealth`(PLT-08)에 기록이 있는 것만 담는다: `db_pool`은
항상, `loop:<name>`은 해당 루프가 최소 1회 tick을 시도한 뒤부터. 이유 —
`run_periodic_loop`(src/services/background_loops.py)는 `sleep(interval)` 후에
tick하므로 막 기동한 프로세스는 어떤 루프도 아직 `LoopHealth`에 항목이 없다.
그런 루프까지 실패로 잡으면 정상 기동 직후에도 항상 503이 된다. 반대로 tick을
한 번이라도 *시도*한 뒤라면(성공이든 실패든) `last_success_age()`가 `+inf`를
포함해 그대로 판정에 쓰인다 — PLT-08 `LoopHealth.last_success_age` docstring이
명시한 의도("readyz의 age < 3×interval 판정이 그대로 실패하도록")를 그대로
따른다. `migration_head`·`event_bus` check는 이 리프 범위 밖(decision: "최소
DB 풀과 loop 신선도") — 후속 리프에서 추가.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.deps import get_pool
from src.core.observability.loop_health import loop_health

router = APIRouter(tags=["health"])


class CheckResult(BaseModel):
    ok: bool
    detail: str | None = None
    observed: float | None = None
    threshold: float | None = None


class ReadinessReport(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckResult]
    as_of: datetime


async def _check_db_pool(pool: asyncpg.Pool) -> CheckResult:
    try:
        await pool.fetchval("SELECT 1")
    except Exception:
        # PLT-02 레닥션: 원인(DSN·드라이버 예외 메시지)은 응답에 싣지 않는다.
        # 실제 원인은 asyncpg/커넥션 계층이 이미 남기는 로그로 추적한다.
        return CheckResult(ok=False, detail="db_pool 연결 실패")
    return CheckResult(ok=True)


def _check_loops() -> dict[str, CheckResult]:
    health = loop_health()
    checks: dict[str, CheckResult] = {}
    for name, loop_status in health.snapshot().items():
        if loop_status.interval_sec <= 0:
            checks[f"loop:{name}"] = CheckResult(ok=True, detail="interval 미설정 — 판정 보류")
            continue
        threshold = 3 * loop_status.interval_sec
        observed = health.last_success_age(name)
        ok = observed < threshold
        checks[f"loop:{name}"] = CheckResult(
            ok=ok,
            detail=None if ok else f"{name}: last_success_age={observed:.1f}s > {threshold:.1f}s",
            observed=None if observed == float("inf") else observed,
            threshold=threshold,
        )
    return checks


@router.get("/livez")
async def livez() -> dict[str, str]:
    """DB 미접촉 liveness — 프로세스가 요청에 응답할 수 있는지만 확인한다."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(pool: asyncpg.Pool = Depends(get_pool)) -> JSONResponse:
    checks = {"db_pool": await _check_db_pool(pool), **_check_loops()}
    ready = all(check.ok for check in checks.values())
    report = ReadinessReport(
        status="ready" if ready else "not_ready",
        checks=checks,
        as_of=datetime.now(timezone.utc),
    )
    status_code = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=report.model_dump(mode="json"))
