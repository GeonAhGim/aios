"""PLT-42 -- regression guard for the `.env.example` audit of JWT_SIGNING_KEYS,
CREDENTIAL_ENCRYPTION_KEYS_PAPER, AIOS_RUNTIME_MODE, AIOS_METRICS_TOKEN.

Three of these vars (JWT_SIGNING_KEYS, CREDENTIAL_ENCRYPTION_KEYS_PAPER,
AIOS_RUNTIME_MODE) are read through a `Mapping[str, str]` function argument
rather than `os.environ` directly, so `scripts/check_consistency.py`'s
`env_key_undocumented` static check (ast-based `os.environ.*` pattern
matching) structurally cannot see them -- which is why this documentation
update needed a manual audit (this leaf) instead of an automated gate.
`AIOS_METRICS_TOKEN` is the one key read via a direct `os.environ.get(...)`
call, and it was in fact one of the four pre-existing `env_key_undocumented`
baseline violations (`src/api/routers/metrics.py:31`) before this audit.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
ENV_EXAMPLE = ROOT / ".env.example"

_AUDITED_KEYS = (
    "JWT_SIGNING_KEYS",
    "JWT_ACTIVE_KID",
    "CREDENTIAL_ENCRYPTION_KEYS_PAPER",
    "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER",
    "AIOS_RUNTIME_MODE",
    "AIOS_METRICS_TOKEN",
)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_module("check_consistency_plt42", SCRIPTS_DIR / "check_consistency.py")


def _write(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=([^#]*)", stripped)
        if m:
            values[m.group(1)] = m.group(2).strip()
    return values


# ---------------------------------------------------------------------------
# happy path -- documented
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", _AUDITED_KEYS)
def test_env_example_documents_audited_key(key: str) -> None:
    assert key in _env_example_values()


# ---------------------------------------------------------------------------
# negative (>= 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", _AUDITED_KEYS)
def test_env_example_slot_has_no_baked_in_value(key: str) -> None:
    """Violating the audit instruction ("add slots without values") would
    commit a secret or a mode default straight into the repository."""
    assert _env_example_values()[key] == ""


def test_env_example_does_not_document_live_scope_credential_keys() -> None:
    """This stage is PAPER-only (FROZEN_PAPER_ONLY). Exposing a LIVE slot in
    `.env.example` invites an operator to fill it in by habit and trip I7
    (LIVE keys must not exist in a PAPER runtime)."""
    keys = set(_env_example_values())
    assert "CREDENTIAL_ENCRYPTION_KEYS_LIVE" not in keys
    assert "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE" not in keys


def test_check_env_keys_structurally_misses_indirect_mapping_reads(tmp_path: Path) -> None:
    """Reproduces a known blind spot: code that reads through a `Mapping`
    argument is invisible to the automated gate. This is exactly why
    JWT_SIGNING_KEYS / CREDENTIAL_ENCRYPTION_KEYS_PAPER / AIOS_RUNTIME_MODE
    stayed undocumented in `.env.example` without CI ever turning red, and
    why this leaf's manual audit was needed."""
    _write(tmp_path, ".env.example", "OTHER_KEY=\n")
    _write(
        tmp_path,
        "src/service.py",
        "from collections.abc import Mapping\n"
        "def load(source: Mapping[str, str]) -> str:\n"
        "    return source.get('JWT_SIGNING_KEYS', '')\n",
    )
    assert cc.check_env_keys(tmp_path) == []


# ---------------------------------------------------------------------------
# failure injection (1)
# ---------------------------------------------------------------------------


def test_check_env_keys_catches_direct_os_environ_regression(tmp_path: Path) -> None:
    """A key read via a direct `os.environ.get(...)` call, like
    AIOS_METRICS_TOKEN, is actually caught when it goes missing from
    `.env.example` (inject the doc gap -> confirm detection)."""
    _write(tmp_path, ".env.example", "OTHER_KEY=\n")
    _write(
        tmp_path,
        "src/api/routers/metrics.py",
        "import os\n"
        "def _require_metrics_token(token):\n"
        "    expected = os.environ.get('AIOS_METRICS_TOKEN')\n",
    )
    hits = cc.check_env_keys(tmp_path)
    assert hits == [("src/api/routers/metrics.py", 3)]

    # Restoring the doc (what this leaf actually did) clears the scan.
    _write(tmp_path, ".env.example", "OTHER_KEY=\nAIOS_METRICS_TOKEN=\n")
    assert cc.check_env_keys(tmp_path) == []


# ---------------------------------------------------------------------------
# performance assertion (1)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_check_env_keys_perf_budget_on_real_repo() -> None:
    """The full `src/` ast scan must stay inside the CI gate step's budget
    (measured ~1.3s locally; an 8s budget leaves ~4x headroom)."""
    started = time.perf_counter()
    cc.check_env_keys(ROOT)
    elapsed = time.perf_counter() - started
    assert elapsed < 8.0


# ---------------------------------------------------------------------------
# gate-red reproduction (1)
# ---------------------------------------------------------------------------


def test_gate_red_repro_for_metrics_token_doc_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Runs `scripts/check_consistency.py` for real to reproduce the failure
    mode this leaf fixes (missing doc -> gate goes red, exit 2), then
    confirms the same run turns green (exit 0) once the doc is restored."""
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({m: 0 for m in cc.METRICS}), encoding="utf-8")

    _write(tmp_path, ".env.example", "OTHER_KEY=\n")
    _write(
        tmp_path,
        "src/api/routers/metrics.py",
        "import os\ndef f():\n    return os.environ.get('AIOS_METRICS_TOKEN')\n",
    )

    rc_red = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])
    assert rc_red == 2
    assert "env_key_undocumented" in capsys.readouterr().out

    _write(tmp_path, ".env.example", "OTHER_KEY=\nAIOS_METRICS_TOKEN=\n")
    rc_green = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])
    assert rc_green == 0
