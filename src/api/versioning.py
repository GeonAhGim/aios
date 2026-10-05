"""API versioning — `/api/v1` formal mount + legacy alias (107 §4).

PLT-16 decision: this leaf exists to lock the contract; `mount_v1` is not yet
wired into `src/main.py` — actual rollout happens sequentially in §9 PLT-17~21
along with per-router envelope migration.

Mounting the same `APIRouter` instance via two prefixes with `include_router`
makes FastAPI register each route independently (separate endpoints differing
only by path). Dependency on the alias registration alone ensures that only
the legacy alias receives `Deprecation`/`Sunset` response headers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from fastapi import APIRouter, Depends, FastAPI, Response

V1_PREFIX = "/api/v1"
# 107 §4 — alias must survive at least one deployment cycle.
# Until the deployment cycle is confirmed, set conservatively 90 days out
# (exact sunset date to be updated after cycle confirmation).
DEFAULT_SUNSET = date(2026, 12, 3)


@dataclass(frozen=True)
class RouterMount:
    """Mount info for a single router passed to `mount_v1`."""

    router: APIRouter
    legacy_prefix: str
    tags: tuple[str, ...] = field(default_factory=tuple)


def mount_v1(
    app: FastAPI,
    mounts: Iterable[RouterMount],
    *,
    sunset: date = DEFAULT_SUNSET,
) -> None:
    """Register each router in `mounts` at `/api/v1<legacy_prefix>`,
    and also register an alias at `legacy_prefix` alone. Only alias responses
    receive `Deprecation: true` and `Sunset: <date>` headers."""
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
