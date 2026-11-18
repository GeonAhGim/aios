"""Main-process heartbeat -- IPC mechanism shared by watchdog 9.1/9.3.

Spec: 기능설계문서_v1.20.md#FD-9.1 (main-process heartbeat via a file timestamp)

The watchdog must run as a separate process fully isolated from main (policy 8.6-A),
so the two communicate through a file timestamp rather than shared memory -- the
simplest mechanism that clearly crosses the process boundary (no over-engineering, 17.9-A).
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

# The only communication path shared by main.py (main process) and watchdog_process.py
# (separate process) -- one file crosses the process memory boundary.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HEARTBEAT_PATH = _PROJECT_ROOT / "runtime" / "main_process.heartbeat"
HEARTBEAT_PATH_ENV = "AIOS_HEARTBEAT_PATH"


def resolve_heartbeat_path() -> Path:
    """Heartbeat file path shared by writer (main) and reader (watchdog).

    ``AIOS_HEARTBEAT_PATH`` wins when set and non-empty, so separate containers can
    point both sides at one shared volume; otherwise the in-repo default is used."""
    override = os.environ.get(HEARTBEAT_PATH_ENV, "").strip()
    return Path(override) if override else DEFAULT_HEARTBEAT_PATH


def write_heartbeat(path: Path) -> None:
    """Called periodically by the main process -- records the current time in the file.

    Red-team audit (docs/RED_TEAM_FINDINGS.md #07): a direct write_text() to the target
    lets the watchdog read between truncate and write, see a corrupt value and raise a
    false HALT. Write a temp file first, then os.replace() it -- atomic on POSIX and
    Windows NTFS, so readers see either the whole old value or the whole new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # The temp name must be unique per writer: with a fixed `<name>.tmp`, two
    # processes writing the same heartbeat (several app instances on one host,
    # or xdist workers each running the app lifespan against the shared
    # `runtime/` dir) race -- one os.replace() consumes the other's temp file
    # and the loser dies with FileNotFoundError (Quality Gate run 36192110851).
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp_path.write_text(str(time.time()), encoding="utf-8")
    # Windows NTFS: a concurrent replace/read on the target briefly raises a
    # sharing violation (PermissionError); retry a few times, then give up --
    # the heartbeat is best-effort and the next tick rewrites it.
    for _attempt in range(5):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError:
            time.sleep(0.005)
        except FileNotFoundError:
            return
    tmp_path.unlink(missing_ok=True)


def read_heartbeat_age_seconds(path: Path) -> float:
    """A missing or corrupt file counts as infinite age (unresponsive is never assumed
    healthy -- common principle of 9.1/9.3)."""
    if not path.exists():
        return float("inf")
    try:
        last = float(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # OSError covers e.g. the path being a directory or a transient
        # read race with a concurrent writer -- fail closed, not fresh.
        return float("inf")
    return time.time() - last
