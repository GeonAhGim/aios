"""PLT-16 — `mount_v1`(`src/api/versioning.py`) 이중 등록·Deprecation 헤더.

DB·네트워크 없음 — 빈 `FastAPI()`에 더미 라우터를 마운트하고 ASGI 왕복만 한다.
`mount_v1`은 아직 `src/main.py`에 배선되지 않았지만(decision, PLT-17~21에서
적용), 함수 자체는 이 리프의 산출물이므로 정식/레거시 경로 분기와 alias에만
`Deprecation`/`Sunset` 헤더가 붙는지(정상 `/api/v1` 경로에는 안 붙는지 —
negative case) 여기서 고정한다.
"""

from __future__ import annotations

from datetime import date

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
