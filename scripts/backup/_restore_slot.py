"""Replication slot cleanup for the restore drill.

pg_basebackup -C -S aios_drill fails with "already exists" when a previous run was killed before
its cleanup ran, so the drill drops the slot both before its work (stale leftovers) and in its
finally block. Results are advisory: a cleanup problem must not flip the drill verdict.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

DROP_SLOT_SQL = (
    "SELECT pg_drop_replication_slot('aios_drill') "
    "WHERE EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name='aios_drill')"
)


def split_dsn_password(dsn: str) -> tuple[str, dict[str, str]]:
    """Return (libpq URL without password, env with PGPASSWORD) so the secret stays out of argv."""
    url = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    parts = urlsplit(url)
    if parts.password is None:
        return url, dict(os.environ)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = (quote(unquote(parts.username or ""), safe="") + "@" if parts.username else "") + host
    if parts.port:
        netloc += f":{parts.port}"
    env = {**os.environ, "PGPASSWORD": unquote(parts.password)}
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)), env


def drop_slot(
    psql_bin: str,
    dsn: str,
    cwd: Path,
    run_cmd: Callable[[list[str], Path, dict | None, int], tuple[int, str]],
) -> dict:
    """Drop the drill slot if present; never raises. Step dict is flagged advisory."""
    url, env = split_dsn_password(dsn)
    rc: int | None = None
    tail: str | None = None
    try:
        rc, tail = run_cmd([psql_bin, url, "-tAc", DROP_SLOT_SQL], cwd, env, 30)
    except OSError as exc:
        tail = f"{type(exc).__name__}: {exc}"
    return {"ok": rc == 0, "rc": rc, "tail": tail, "advisory": True}
