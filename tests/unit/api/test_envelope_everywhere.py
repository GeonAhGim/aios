"""PLT-21(admin.py + task-1108: foundation/connections·mandates·evidence +
task-1217: foundation/paper_control·performance·reconciliation 스콥) —
해당 라우터 엔드포인트가 §3.3 봉투(`ApiResponse[...]`)를 쓰는지 회귀 가드.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21
(row: "test_envelope_everywhere.py: app.routes 전수 순회, 응답 모델이
ApiResponse[...]이거나 /healthz|/metrics 예외 목록").

전수(全數) 스윕은 이 리프의 스콥이 아니다 — marketplace/strategy_builder/
suitability/executions/portfolio/reports/notifications/alerts/device_tokens/
wallet/exchange_credentials/foundation/risk_gate·trust·validation 등 다른
레거시 라우터는 아직 raw HTTPException만 EXCEPTION_MAP으로 이관했을 뿐
(또는 이관 예정일 뿐) `ApiResponse[...]` 봉투로 응답을 감싸지 않는다(각자
도메인 모델을 직접 반환) — 그 전수 이관은 이 리프들의 DoD 밖이라 여기서
강제하면 바로 실패하는 거짓 기대를 심는 꼴이 된다. `src.main`을 통째로
import하지 않는 이유는 `tests/unit/api/contracts/test_handlers.py`와
동일(lifespan에 실제 secrets/DB pool 필요).
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from src.api.contracts.envelope import ApiResponse
from src.api.routers import admin, health, metrics
from src.api.routers.foundation import (
    connections,
    evidence,
    mandates,
    paper_control,
    performance,
    reconciliation,
)


def _api_routes(module) -> list[APIRoute]:
    return [route for route in module.router.routes if isinstance(route, APIRoute)]


def _wraps_api_response(route: APIRoute) -> bool:
    model = route.response_model
    if model is None:
        return False
    origin = getattr(model, "__pydantic_generic_metadata__", {}).get("origin")
    return origin is ApiResponse


def test_admin_router_all_routes_use_api_response_envelope():
    offenders = [
        f"{sorted(route.methods)} {route.path}"
        for route in _api_routes(admin)
        if not _wraps_api_response(route)
    ]
    assert offenders == []


def test_foundation_connections_mandates_evidence_routes_use_api_response_envelope():
    offenders = [
        f"{module.__name__} {sorted(route.methods)} {route.path}"
        for module in (connections, mandates, evidence)
        for route in _api_routes(module)
        if not _wraps_api_response(route)
    ]
    assert offenders == []


def test_foundation_paper_control_performance_reconciliation_routes_use_api_response_envelope():
    offenders = [
        f"{module.__name__} {sorted(route.methods)} {route.path}"
        for module in (paper_control, performance, reconciliation)
        for route in _api_routes(module)
        if not _wraps_api_response(route)
    ]
    assert offenders == []


def test_healthz_and_metrics_routes_stay_exempt_from_envelope():
    health_paths = {route.path for route in _api_routes(health)}
    metrics_paths = {route.path for route in _api_routes(metrics)}
    assert health_paths == {"/livez", "/readyz"}
    assert metrics_paths == {"/metrics"}
    for route in [*_api_routes(health), *_api_routes(metrics)]:
        assert not _wraps_api_response(route)
