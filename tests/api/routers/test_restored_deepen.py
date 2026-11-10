"""DEEPEN: tests/api/routers/__init__.py

Negative / failure-injection tests for `register_routers()`
(src/api/router_registry.py) -- the single choke point that wires every
router module onto the FastAPI app. A silently-swallowed import error or a
duck-typed non-router object here would ship an app with a missing route
(the exact "ghost path" class of defect `check_consistency.py` is meant to
catch at the openapi-contract level; this file catches it one layer lower,
at the Python call itself).

DoD checklist (task-10285, orphan leaf task-7301 "고아 산출물 회수 7070
(frontend-1)"):
- [x] negative test 3건 이상 추가 (불변식 위반 입력을 명시적으로 거부하는 케이스)
- [x] 실패주입 케이스 1건 이상 추가 (monkeypatch로 의존성 예외 유발 등)
- [x] `python -m pytest tests/api/routers/__init__.py -q` 통과
- [x] docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest
from fastapi import FastAPI

import src.api.routers.auth as auth_router_module
from src.api.router_registry import register_routers


class TestRegisterRoutersNegative:
    """register_routers가 계약 위반 입력을 명시적으로 거부하는지 검증.

    fail-closed 기본값(CLAUDE.md #3) -- app이 아닌 객체를 넘기거나 라우터
    모듈이 깨져 있을 때 조용히 no-op 하거나 부분 배선된 앱을 돌려주면 안 된다.
    """

    def test_rejects_none_app(self) -> None:
        with pytest.raises(AttributeError):
            register_routers(None)

    def test_rejects_non_fastapi_app(self) -> None:
        with pytest.raises(AttributeError):
            register_routers(object())

    def test_rejects_broken_router_object(self) -> None:
        """라우터 모듈이 `router`를 APIRouter가 아닌 값으로 export하면 거부돼야 한다."""
        original = auth_router_module.router
        auth_router_module.router = "not-a-router"
        try:
            with pytest.raises(AttributeError):
                register_routers(FastAPI())
        finally:
            auth_router_module.router = original

    def test_rejects_missing_router_attribute(self) -> None:
        """라우터 모듈에 `router` 속성 자체가 없으면 AttributeError로 거부돼야 한다."""
        original = auth_router_module.router
        del auth_router_module.router
        try:
            with pytest.raises(AttributeError):
                register_routers(FastAPI())
        finally:
            auth_router_module.router = original


class TestRegisterRoutersFailureInjection:
    """의존성(서브모듈 import) 예외가 삼켜지지 않고 전파되는지 검증."""

    def test_propagates_import_error_without_swallowing(self) -> None:
        real_import = builtins.__import__

        def failing_import(
            name: str,
            globals: Any = None,  # noqa: A002
            locals: Any = None,  # noqa: A002
            fromlist: Any = (),
            level: int = 0,
        ) -> Any:
            if name == "src.api.routers" and fromlist and "health" in fromlist:
                raise ImportError("injected: health router unavailable")
            return real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = failing_import
        try:
            with pytest.raises(ImportError, match="injected"):
                register_routers(FastAPI())
        finally:
            builtins.__import__ = real_import


class TestRegisterRoutersSanity:
    """정상 배선 경로가 여전히 동작함을 확인하는 기준선(baseline) 테스트."""

    def test_mounts_known_routers_on_fresh_app(self) -> None:
        app = FastAPI()
        register_routers(app)
        schema = app.openapi()
        assert any(path.startswith("/auth") for path in schema["paths"])
        assert len(schema["paths"]) > 50
