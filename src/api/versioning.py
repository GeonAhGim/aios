"""API 버저닝 — `/api/v1` 정식 마운트 + 레거시 alias(107 §4).

PLT-16 decision: 이 리프는 계약을 고정하는 것이 목적이라 `mount_v1`을 아직
`src/main.py`에 배선하지 않는다 — 실제 적용은 §9 PLT-17~21에서 라우터별
봉투 이관과 함께 순차 진행한다.

같은 `APIRouter` 인스턴스를 두 프리픽스로 두 번 `include_router`하면 FastAPI가
라우트를 각각 독립적으로 등록한다(경로만 다른 별개 엔드포인트). 레거시 alias
쪽에만 `Deprecation`/`Sunset` 응답 헤더가 붙도록 alias 등록에만 의존성을 건다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from fastapi import APIRouter, Depends, FastAPI, Response

V1_PREFIX = "/api/v1"
# 107 §4 — alias는 최소 1 배포 주기 유지. 배포 주기가 확정되기 전까지는
# 보수적으로 90일 뒤로 둔다(정확한 해제일은 배포 주기 확정 후 갱신).
DEFAULT_SUNSET = date(2026, 12, 3)


@dataclass(frozen=True)
class RouterMount:
    """`mount_v1`에 넘기는 라우터 하나의 마운트 정보."""

    router: APIRouter
    legacy_prefix: str
    tags: tuple[str, ...] = field(default_factory=tuple)


def mount_v1(
    app: FastAPI,
    mounts: Iterable[RouterMount],
    *,
    sunset: date = DEFAULT_SUNSET,
) -> None:
    """`mounts`의 각 라우터를 `/api/v1<legacy_prefix>`에 정식 등록하고,
    `legacy_prefix` 그대로도 별칭 등록한다. 별칭 응답에만 `Deprecation: true`,
    `Sunset: <date>` 헤더가 붙는다."""
    sunset_value = sunset.isoformat()

    async def _mark_deprecated(response: Response) -> None:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = sunset_value

    for mount in mounts:
        app.include_router(
            mount.router,
            prefix=f"{V1_PREFIX}{mount.legacy_prefix}",
            tags=list(mount.tags),
        )
        app.include_router(
            mount.router,
            prefix=mount.legacy_prefix,
            tags=[*mount.tags, "deprecated"],
            dependencies=[Depends(_mark_deprecated)],
        )
