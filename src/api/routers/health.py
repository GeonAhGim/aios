"""PLT-09 — Health-check endpoints: `/readyz` (readiness) and `/livez` (liveness).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.2, §9 PLT-09.

`ReadinessReport`/`CheckResult` follow the existing contract (task-466) already
consumed by frontend/packages/api-client/src/readiness.ts as SSOT — field names
are not renamed. `/readyz` and `/livez` are exceptions to the §3.3 `ApiResponse`
wrapper (operational probes), so the router returns `JSONResponse` directly.

`checks` currently includes only what `LoopHealth` (PLT-08) has recorded: `db_pool`
is always present; `loop:<name>` appears only after the loop has attempted at least
one tick. Reason — `run_periodic_loop` (src/services/background_loops.py) ticks
*after* `sleep(interval)`, so a freshly started process has no `LoopHealth` entries
yet. Flagging such loops as failures would produce a constant 503 immediately after
normal startup. Conversely, once a loop has attempted *at least one* tick (success or
failure), `last_success_age()` — including `+inf` — is used in the decision as-is,
following the intent explicitly stated in the PLT-08 `LoopHealth.last_success_age`
docstring ("to let the readyz age < 3×interval check fail naturally").
`migration_head` and `event_bus` checks are out of scope for this leaf
(decision: "minimum DB pool and loop freshness") — to be added in a follow-up leaf.
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
        # PLT-02 reduction: do not include root cause (DSN, driver exception message)
        # in the response. The root cause is already logged by the asyncpg/connection
        # layer for tracing.
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
    """Liveness without DB contact — confirms the process can still respond to requests."""
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
