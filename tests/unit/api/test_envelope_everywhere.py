"""PLT-21(admin.py + task-1108: foundation/connections·mandates·evidence +
task-1217: foundation/paper_control·performance·reconciliation +
task-1218: foundation/risk_gate·trust·validation 스콥) — 해당 라우터
엔드포인트가 §3.3 봉투(`ApiResponse[...]`)를 쓰는지 회귀 가드.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21
(row: "test_envelope_everywhere.py: app.routes 전수 순회, 응답 모델이
ApiResponse[...]이거나 /healthz|/metrics 예외 목록").

전수(全數) 스윕은 이 리프의 스콥이 아니다 — marketplace/strategy_builder/
suitability/executions/portfolio/reports/notifications/alerts/device_tokens/
wallet/exchange_credentials 등 다른 레거시 라우터는 아직 raw HTTPException만
EXCEPTION_MAP으로 이관했을 뿐(또는 이관 예정일 뿐) `ApiResponse[...]` 봉투로
응답을 감싸지 않는다(각자 도메인 모델을 직접 반환) — 그 전수 이관은 이
리프들의 DoD 밖이라 여기서 강제하면 바로 실패하는 거짓 기대를 심는 꼴이
된다. `src.main`을 통째로 import하지 않는 이유는 `tests/unit/api/contracts/
test_handlers.py`와 동일(lifespan에 실제 secrets/DB pool 필요).
"""

from __future__ import annotations

import copy

from fastapi import APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel

from src.api.contracts.envelope import ApiResponse
from src.api.routers import admin, health, metrics
from src.api.routers.foundation import (
    connections,
    evidence,
    mandates,
    paper_control,
    performance,
    reconciliation,
    risk_gate,
    trust,
    validation,
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


def test_foundation_risk_gate_trust_validation_routes_use_api_response_envelope():
    offenders = [
        f"{module.__name__} {sorted(route.methods)} {route.path}"
        for module in (risk_gate, trust, validation)
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


class _PlainPayload(BaseModel):
    """봉투로 감싸지 않은 도메인 모델 -- 위반 케이스를 만드는 용도."""

    value: str


def _fake_route(**route_kwargs) -> APIRoute:
    router = APIRouter()

    async def _endpoint():
        return None

    router.add_api_route("/fake", _endpoint, methods=["GET"], **route_kwargs)
    return next(route for route in router.routes if isinstance(route, APIRoute))


def test_wraps_api_response_rejects_plain_basemodel_response_model():
    route = _fake_route(response_model=_PlainPayload)
    assert not _wraps_api_response(route)


def test_wraps_api_response_rejects_route_with_no_response_model():
    route = _fake_route(response_model=None)
    assert not _wraps_api_response(route)


def test_wraps_api_response_rejects_dict_response_model():
    route = _fake_route(response_model=dict)
    assert not _wraps_api_response(route)


def test_admin_router_offender_scan_fails_closed_when_response_model_stripped() -> None:
    """실패주입: admin 라우터의 한 라우트에서 response_model이 사라지는
    상황(의존성/미들웨어가 런타임에 이를 건드리는 경우 등)을 흉내 내도,
    봉투 가드가 이를 조용히 통과시키지 않고 offender로 잡아내야 한다 --
    fail-closed. 프로세스 전역 싱글턴인 실제 admin 라우터 객체를 직접
    monkeypatch하면(이전 구현) 테스트 격리가 깨져 같은 워커에서 뒤에 도는
    다른 테스트/커버리지 계측에 영향을 줄 수 있으므로, 같은 구조의 복사본만
    변형한다(task-7873: coverage_ratchet 회귀 근본 정정)."""
    routes = _api_routes(admin)
    assert routes, "admin router must expose at least one route to inject the failure into"
    stripped = copy.copy(routes[0])
    stripped.response_model = None
    candidate_routes = [stripped, *routes[1:]]

    offenders = [
        f"{sorted(route.methods)} {route.path}"
        for route in candidate_routes
        if not _wraps_api_response(route)
    ]
    assert offenders == [f"{sorted(stripped.methods)} {stripped.path}"]
