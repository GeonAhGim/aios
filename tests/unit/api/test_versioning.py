"""PLT-16 — `mount_v1`(`src/api/versioning.py`) 이중 등록·Deprecation 헤더.

DB·네트워크 없음 — 빈 `FastAPI()`에 더미 라우터를 마운트하고 ASGI 왕복만 한다.
`mount_v1`은 아직 `src/main.py`에 배선되지 않았지만(decision, PLT-17~21에서
적용), 함수 자체는 이 리프의 산출물이므로 정식/레거시 경로 분기와 alias에만
`Deprecation`/`Sunset` 헤더가 붙는지(정상 `/api/v1` 경로에는 안 붙는지 —
negative case) 여기서 고정한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.versioning import DEFAULT_SUNSET, RouterMount, mount_v1


def _make_app(*, sunset: date | None = None) -> FastAPI:
    router = APIRouter()

    @router.get("/widgets")
    async def list_widgets() -> dict[str, bool]:
        return {"ok": True}

    app = FastAPI()
    mounts = [RouterMount(router=router, legacy_prefix="/widgets_root", tags=("widgets",))]
    if sunset is None:
        mount_v1(app, mounts)
    else:
        mount_v1(app, mounts, sunset=sunset)
    return app


async def test_v1_path_responds_without_deprecation_headers() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/widgets_root/widgets")

    assert response.status_code == 200
    assert "deprecation" not in response.headers
    assert "sunset" not in response.headers


async def test_legacy_alias_responds_with_deprecation_headers() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/widgets_root/widgets")

    assert response.status_code == 200
    assert response.headers["deprecation"] == "true"
    assert response.headers["sunset"] == DEFAULT_SUNSET.isoformat()


async def test_legacy_alias_uses_custom_sunset_override() -> None:
    custom_sunset = date(2027, 1, 15)
    app = _make_app(sunset=custom_sunset)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/widgets_root/widgets")

    assert response.headers["sunset"] == "2027-01-15"


async def test_missing_path_404_on_both_prefixes() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        v1_response = await client.get("/api/v1/widgets_root/does-not-exist")
        legacy_response = await client.get("/widgets_root/does-not-exist")

    assert v1_response.status_code == 404
    assert legacy_response.status_code == 404


def test_legacy_prefix_without_leading_slash_is_rejected() -> None:
    """불변식: `legacy_prefix`는 반드시 `/`로 시작해야 한다 — FastAPI의
    `include_router`가 잘못된 prefix를 조용히 받아들이지 않고 즉시 거부하는지
    고정한다(negative case)."""
    router = APIRouter()

    @router.get("/widgets")
    async def list_widgets() -> dict[str, bool]:
        return {"ok": True}

    app = FastAPI()
    mounts = [RouterMount(router=router, legacy_prefix="widgets_root", tags=("widgets",))]

    try:
        mount_v1(app, mounts)
    except AssertionError as exc:
        assert "must start with" in str(exc)
    else:
        raise AssertionError("expected AssertionError for prefix without leading '/'")


def test_invalid_sunset_type_is_rejected() -> None:
    """불변식: `sunset`은 `date`여야 한다 — 문자열처럼 `.isoformat()`이 없는
    값을 넘기면 조용히 오동작하지 않고 명시적으로 실패해야 한다(negative case)."""
    router = APIRouter()

    @router.get("/widgets")
    async def list_widgets() -> dict[str, bool]:
        return {"ok": True}

    app = FastAPI()
    mounts = [RouterMount(router=router, legacy_prefix="/widgets_root", tags=("widgets",))]

    bad_sunset: Any = "2026-12-03"
    try:
        mount_v1(app, mounts, sunset=bad_sunset)
    except AttributeError:
        pass
    else:
        raise AssertionError("expected AttributeError for non-date sunset value")


async def test_empty_mounts_registers_no_routes() -> None:
    """불변식: `mounts`가 비어 있으면 어떤 경로도 등록되지 않는다 — 빈 목록이
    조용히 와일드카드로 흡수되지 않는지 확인한다(negative case)."""
    app = FastAPI()
    mount_v1(app, [])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        v1_response = await client.get("/api/v1/widgets_root/widgets")
        legacy_response = await client.get("/widgets_root/widgets")

    assert v1_response.status_code == 404
    assert legacy_response.status_code == 404


async def test_wrong_http_method_returns_405_without_deprecation_headers() -> None:
    """실패주입 성격의 negative case: 마운트된 경로라도 허용되지 않은 메서드는
    405로 거부되어야 하며, 라우트 매칭에 실패한 응답에 `Deprecation`/`Sunset`
    헤더가 새어 나오지 않아야 한다."""
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        v1_response = await client.post("/api/v1/widgets_root/widgets")
        legacy_response = await client.post("/widgets_root/widgets")

    assert v1_response.status_code == 405
    assert "deprecation" not in v1_response.headers
    assert legacy_response.status_code == 405


def test_include_router_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패주입: `FastAPI.include_router`가 예외를 던지면 `mount_v1`은 이를
    삼키지 않고 그대로 전파해야 한다(fail-closed — 부분 마운트 상태로 조용히
    넘어가지 않는다)."""
    router = APIRouter()

    @router.get("/widgets")
    async def list_widgets() -> dict[str, bool]:
        return {"ok": True}

    app = FastAPI()
    mounts = [RouterMount(router=router, legacy_prefix="/widgets_root", tags=("widgets",))]

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("include_router exploded")

    monkeypatch.setattr(app, "include_router", _boom)

    try:
        mount_v1(app, mounts)
    except RuntimeError as exc:
        assert "exploded" in str(exc)
    else:
        raise AssertionError("expected RuntimeError to propagate from include_router")
