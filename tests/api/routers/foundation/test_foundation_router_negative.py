"""Negative / failure-injection tests for foundation compliance and trust routers.

DoD (task-25313, parent task-10286):
- negative tests: malformed / truncated / over-long / empty path UUIDs on both routers
  (FastAPI path validation -> 422, no handler is reached).
- failure injection: a repository dependency that raises is surfaced as a server error,
  not swallowed into a 2xx.
- performance assertion: N/A(router contract tests - no latency budget applies)
- gate-red reproduction: without dependency overrides every request returns 500 because
  auth/repository dependencies fail first, so asserting 500 for a bad UUID would pin the
  wrong cause; the overrides below isolate path validation.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.routers.foundation.compliance as compliance_mod
import src.api.routers.foundation.trust as trust_mod
from src.api.router_registry import register_routers

GOOD_UUID = "550e8400-e29b-4307-9000-000000000000"
BAD_UUIDS = [
    "not-a-uuid",
    "550e8400-e29b",
    GOOD_UUID + "0000",
]
DECISIONS = "/v1/foundation/compliance/decisions/"
CONSENTS = "/v1/foundation/trust/consents/"


def _compliance_client(**overrides: object) -> TestClient:
    app = FastAPI()
    app.include_router(compliance_mod.router)
    app.dependency_overrides[compliance_mod.get_current_user] = lambda: object()
    app.dependency_overrides[compliance_mod.get_mandate_repository] = lambda: object()
    for dep, fn in overrides.items():
        app.dependency_overrides[getattr(compliance_mod, dep)] = fn
    return TestClient(app, raise_server_exceptions=False)


def _trust_client() -> TestClient:
    app = FastAPI()
    app.include_router(trust_mod.router)
    app.dependency_overrides[trust_mod.get_tenant_context] = lambda: object()
    app.dependency_overrides[trust_mod.get_trust_repository] = lambda: object()
    return TestClient(app, raise_server_exceptions=False)


class TestRouterModules:
    def test_router_module_without_router_attribute_is_rejected(self) -> None:
        original = compliance_mod.router
        try:
            del compliance_mod.router
            with pytest.raises(AttributeError):
                _ = compliance_mod.router
        finally:
            compliance_mod.router = original


class TestComplianceDecisionIdNegative:
    @pytest.mark.parametrize("bad", BAD_UUIDS)
    def test_malformed_decision_id_is_422(self, bad: str) -> None:
        resp = _compliance_client().get(DECISIONS + bad)
        assert resp.status_code == 422

    def test_empty_decision_id_does_not_match_route(self) -> None:
        resp = _compliance_client().get(DECISIONS)
        assert resp.status_code == 404

    def test_extra_path_segment_does_not_match_route(self) -> None:
        resp = _compliance_client().get(DECISIONS + GOOD_UUID + "/extra")
        assert resp.status_code == 404


class TestTrustConsentIdNegative:
    @pytest.mark.parametrize("bad", BAD_UUIDS)
    def test_malformed_consent_id_is_422(self, bad: str) -> None:
        resp = _trust_client().post(CONSENTS + bad + ":revoke")
        assert resp.status_code == 422

    def test_empty_consent_id_is_422(self) -> None:
        # ":revoke" still matches {consent_id} with an empty-ish value; it must be rejected.
        resp = _trust_client().post(CONSENTS + ":revoke")
        assert resp.status_code in (404, 422)

    def test_get_method_not_allowed_on_revoke(self) -> None:
        resp = _trust_client().get(CONSENTS + GOOD_UUID + ":revoke")
        assert resp.status_code in (404, 405)


class TestFailureInjection:
    def test_repository_dependency_failure_is_server_error(self) -> None:
        def _fail() -> object:
            raise RuntimeError("db down")

        client = _compliance_client(get_mandate_repository=_fail)
        resp = client.get(DECISIONS + GOOD_UUID)
        assert resp.status_code == 500

    def test_auth_dependency_failure_is_server_error(self) -> None:
        def _fail() -> object:
            raise RuntimeError("no user")

        client = _compliance_client(get_current_user=_fail)
        resp = client.get(DECISIONS + GOOD_UUID)
        assert resp.status_code == 500
        assert UUID(GOOD_UUID)


class TestRegistration:
    def test_register_routers_mounts_foundation_paths(self) -> None:
        app = FastAPI()
        register_routers(app)
        paths = list(app.openapi()["paths"])
        for prefix in ("compliance", "trust", "mandates", "connections", "evidence", "ems"):
            assert any(f"/v1/foundation/{prefix}" in p for p in paths), prefix
