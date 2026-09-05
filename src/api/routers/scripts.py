"""DSL-12 — `POST /v1/scripts/compile`: AIOS Script 컴파일(해시·산정치·오류 위치).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.4 DSL-12,
§3.3(에러 taxonomy 4종·위치 정보).

71번 §6 규칙: 라우터는 auth/TenantContext 주입·transport validation·
`compile_source`(src/core/script/artifact/compile.py) 호출만 한다. 저장은
없다(아티팩트 영속화는 MP/DSL 후속, task-1535 decision) — 순수 컴파일 결과만
`ok()` 봉투로 돌려준다. 도메인 예외(`ScriptCompileError`)는 잡지 않는다 —
`exception_registry.py`(EXCEPTION_MAP)가 `VALIDATION_INVALID_FIELD`(400)로
번역하고 `details.code/line/col`을 봉투에 싣는다(raw HTTPException 0건,
PLT-21 가드 대상).

인증: `get_tenant_context`(PLT-28) — 게이트웨이 인증 + 테넌트 컨텍스트.
컴파일은 테넌트 데이터를 읽지 않지만 §5 로그 공통 필드(tenant_id·
script_hash)를 남기기 위해 컨텍스트를 받는다. 레지스트리 버전은 IND-1
`DEFAULT_REGISTRY.registry_hash()`(스펙 정준 해시)를 그대로 쓴다.

성능(DoD "컴파일 ≤300ms"): `elapsed_ms`를 응답에 실어 클라이언트·테스트가
실측만 한다 — 이 라우터는 지연을 단언하지 않는다.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends

from src.api.contracts.envelope import ApiResponse, ok
from src.api.foundation_deps import get_tenant_context
from src.api.schemas.scripts import CompileScriptRequest, CompileScriptView
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorRegistry
from src.core.script.artifact.compile import compile_source
from src.foundation.trust.contracts.v1 import TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/scripts", tags=["scripts"])


def get_indicator_registry() -> IndicatorRegistry:
    """IND-1 기본 레지스트리(프로세스 단일 인스턴스). 테스트가 덮어쓸 수 있는 의존성."""
    return DEFAULT_REGISTRY


@router.post("/compile", response_model=ApiResponse[CompileScriptView])
async def compile_script(
    body: CompileScriptRequest,
    tenant: TenantContext = Depends(get_tenant_context),
    registry: IndicatorRegistry = Depends(get_indicator_registry),
) -> ApiResponse[CompileScriptView]:
    started = time.perf_counter()
    compiled = compile_source(body.source, registry_version=registry.registry_hash())
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "script.compile",
        extra={
            "event_type": "script.compile",
            "tenant_id": str(tenant.tenant_id),
            "script_hash": compiled.script_hash,
            "duration_ms": elapsed_ms,
        },
    )
    return ok(CompileScriptView.from_compiled(compiled, elapsed_ms=elapsed_ms))


__all__ = ["get_indicator_registry", "router"]
