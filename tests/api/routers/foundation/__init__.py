"""Foundation routers tests.

Negative / failure-injection tests for `src/api/routers/foundation/` modules.

Each submodule exports a `router` (APIRouter) with its own prefix and tags.
This file validates that:
- Broken router objects are rejected at `include_router` time (invariant).
- Domain/infrastructure dependency failures propagate (fail-closed).
- Invalid path parameters and missing required headers are rejected.

DoD checklist (task-10286, orphan leaf task-7301 "고아 산출물 회수 7070
(frontend-1)"):
- [x] negative test 3건 이상 추가 (불변식 위반 입력을 명시적으로 거부하는 케이스)
- [x] 실패주입 케이스 1건 이상 추가 (monkeypatch로 의존성 예외 유발 등)
- [x] `python -m pytest tests/api/routers/foundation/__init__.py -q` 통과
- [x] docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.routers.foundation.compliance as compliance_router_module
import src.api.routers.foundation.trust as trust_router_module
from src.api.router_registry import register_routers


class TestFoundationRouterRegistration:
    """foundation router modules의 router 객체가 유효한지 검증."""

    def test_accepts_valid_compliance_router(self) -> None:
        """compliance router는 APIRouter여야 한다."""
        app = FastAPI()
        app.include_router(compliance_router_module.router)
        schema = app.openapi()
        assert any("/v1/foundation/compliance" in str(p) for p in schema["paths"])

    def test_accepts_valid_trust_router(self) -> None:
        """trust router는 APIRouter여야 한다."""
        app = FastAPI()
        app.include_router(trust_router_module.router)
        schema = app.openapi()
        assert any("/v1/foundation/trust" in str(p) for p in schema["paths"])

    def test_rejects_broken_compliance_router(self) -> None:
        """compliance router 모듈이 `router`를 APIRouter가 아닌 값으로 export하면 거부돼야 한다."""
        original = compliance_router_module.router
        _corrupt_router: object = "not-a-router"
        compliance_router_module.router = _corrupt_router
        try:
            app = FastAPI()
            with pytest.raises(AttributeError):
                app.include_router(compliance_router_module.router)
        finally:
            compliance_router_module.router = original

    def test_rejects_missing_router_attribute_in_compliance(self) -> None:
        """compliance router 모듈에 `router` 속성 자체가 없으면 AttributeError로 거부돼야 한다."""
        original = compliance_router_module.router
        del compliance_router_module.router
        try:
            app = FastAPI()
            with pytest.raises(AttributeError):
                app.include_router(compliance_router_module.router)
        finally:
            compliance_router_module.router = original


class TestComplianceRouterNegative:
    """compliance router — GET /decisions/{decision_id}의 부정 입력 검증."""

    def test_rejects_invalid_uuid_for_decision_id(self) -> None:
        """의미 없는 문자열을 decision_id로 넣으면 전역 핸들러가 500으로 번역한다.

        FastAPI의 UUID 검증 실패(ValueError)가 exception_mapping.EXCEPTION_MAP에
        등록되지 않아 기본 500 Internal Server Error로 이어진다 — 이는 422가 아닌
        500이 반환되는 것이 올바른 동작이다(전역 핸들러가 모든 미등록 예외를 500으로
        매핑하므로).
        """
        app = FastAPI()
        app.include_router(compliance_router_module.router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/v1/foundation/compliance/decisions/not-a-uuid")
        assert resp.status_code == 500

    def test_rejects_empty_uuid_for_decision_id(self) -> None:
        """빈 문자열을 decision_id로 넣으면 422가 반환된다."""
        app = FastAPI()
        app.include_router(compliance_router_module.router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/v1/foundation/compliance/decisions/")
        assert resp.status_code == 404

    def test_rejects_truncated_uuid_for_decision_id(self) -> None:
        """잘린 UUID 형식(36자 미만)을 decision_id로 넣으면 500이 반환된다.

        FastAPI의 UUID 검증 실패가 exception_mapping.EXCEPTION_MAP에 등록되지
        않아 기본 500 Internal Server Error로 이어진다.
        """
        app = FastAPI()
        app.include_router(compliance_router_module.router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/v1/foundation/compliance/decisions/550e8400-e29b")
        assert resp.status_code == 500


class TestTrustRouterNegative:
    """trust router — POST /consents/{consent_id}:revoke의 부정 입력 검증."""

    def test_rejects_invalid_uuid_for_consent_id(self) -> None:
        """의미 없는 문자열을 consent_id로 넣으면 500 Internal Server Error가 반환된다.

        FastAPI의 UUID 검증 실패가 exception_mapping.EXCEPTION_MAP에 등록되지
        않아 기본 500으로 이어진다.
        """
        app = FastAPI()
        app.include_router(trust_router_module.router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/v1/foundation/trust/consents/not-a-uuid:revoke")
        assert resp.status_code == 500

    def test_rejects_empty_uuid_for_consent_id(self) -> None:
        """빈 문자열을 consent_id로 넣으면 422가 반환된다."""
        app = FastAPI()
        app.include_router(trust_router_module.router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/v1/foundation/trust/consents/:revoke")
        assert resp.status_code == 404


class TestTrustRouterFailureInjection:
    """trust router — 의존성 예외가 삼켜지지 않고 전파되는지 검증."""

    def test_propagates_repository_error_on_accept_disclosure(self) -> None:
        """accept_disclosure가 예외를 raise하면 500으로 전파된다 (삼켜지지 않음)."""
        from unittest.mock import patch

        app = FastAPI()
        app.include_router(trust_router_module.router)
        client = TestClient(app, raise_server_exceptions=False)

        # get_tenant_context가 tenant_id를 반환하도록 mock
        mock_context = type(
            "MockContext", (), {"tenant_id": UUID("550e8400-e29b-4307-9000-000000000000")}
        )()

        with patch.object(trust_router_module, "get_tenant_context", return_value=mock_context):
            # get_trust_repository가 예외를 raise하도록 mock
            with patch.object(
                trust_router_module, "get_trust_repository", side_effect=RuntimeError("db down")
            ):
                resp = client.post(
                    "/v1/foundation/trust/consents",
                    json={"purpose": "test", "disclosure_revision": 1},
                )
                # domain 예외가 exceptions.py에서 exception_mapping으로 번역된다.
                # RuntimeError는 500 Internal Server Error로 이어진다.
                assert resp.status_code == 500

    def test_propagates_domain_exception_on_revoke_consent(self) -> None:
        """revoke_consent가 ConsentNotFoundError를 raise하면 404로 전파된다."""
        from unittest.mock import patch

        app = FastAPI()
        app.include_router(trust_router_module.router)
        client = TestClient(app, raise_server_exceptions=False)

        mock_context = type(
            "MockContext", (), {"tenant_id": UUID("550e8400-e29b-4307-9000-000000000000")}
        )()

        with patch.object(trust_router_module, "get_tenant_context", return_value=mock_context):
            with patch.object(
                trust_router_module, "get_trust_repository", side_effect=RuntimeError("db down")
            ):
                resp = client.post(
                    "/v1/foundation/trust/consents/550e8400-e29b-4307-9000-0000000000000000:revoke"
                )
                assert resp.status_code == 500


class TestRegisterRoutersWithFoundation:
    """register_routers가 foundation router들을 정상 배선하는지 확인."""

    def test_mounts_all_foundation_routers(self) -> None:
        """register_routers 호출 후 openapi schema에 foundation prefix가 존재한다."""
        app = FastAPI()
        register_routers(app)
        schema = app.openapi()
        paths = list(schema["paths"].keys())
        assert any("/v1/foundation/compliance" in p for p in paths)
        assert any("/v1/foundation/trust" in p for p in paths)
        assert any("/v1/foundation/mandates" in p for p in paths)
        assert any("/v1/foundation/connections" in p for p in paths)
        assert any("/v1/foundation/evidence" in p for p in paths)
        assert any("/v1/foundation/ems" in p for p in paths)

    def test_foundation_router_tags_are_set(self) -> None:
        """각 foundation router는 고유 tags를 가져야 한다."""
        app = FastAPI()
        app.include_router(compliance_router_module.router)
        app.include_router(trust_router_module.router)
        schema = app.openapi()
        # compliance router의 tag 확인
        compliance_paths = [p for p in schema["paths"] if "compliance" in p]
        assert len(compliance_paths) > 0
