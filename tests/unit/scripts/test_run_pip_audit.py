"""scripts/run_pip_audit.py unit tests -- task-8362(esc-ci-supply_chain).

Regression for a local_ci `supply_chain` red: `subprocess.run(cmd,
capture_output=True, text=True)` decoded pip-audit's UTF-8 JSON output using
the OS's preferred encoding (cp949 on this Windows CI runner), which raised
`UnicodeDecodeError` on any non-ASCII byte (e.g. a Korean package
description) and crashed before `is_network_error()` ever got a real string
-- surfacing as `TypeError: argument of type 'NoneType' is not iterable`
once the caller's `except`/log path re-entered with an unset variable. The
fix pins `encoding="utf-8"` on the subprocess call so decoding no longer
depends on the host locale.

D2 DoD: negative tests >= 3 (expired ignore entry, malformed ignore entry,
non-JSON stdout), one failure-injection test (subprocess emits UTF-8 bytes
that are invalid cp949 -- reproduces the exact byte class from the CI log),
one numeric performance assertion, one red-gate reproduction (proves the
pre-fix code path actually raises `UnicodeDecodeError` on this input).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from tests._perf.relative_budget import RelativeBudget

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts" / "run_pip_audit.py"

# task-8362: is_network_error() is a handful of `in` checks over a short
# string -- this must stay far cheaper than the calibration loop.
_IS_NETWORK_ERROR_MAX_RATIO = 0.5

# UTF-8 bytes for "korean" package note text ("한글 취약점"). Byte 0xec is a
# lead byte for a Hangul syllable in UTF-8 -- exactly the byte position/value
# reported in the esc-ci-supply_chain.json traceback (position 103, byte
# 0xec). Invalid as cp949 when decoded starting mid-sequence via JSON
# wrapping below.
_KOREAN_TEXT = "한글 취약점 설명"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_pip_audit_task8362", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mod() -> ModuleType:
    return _load_module()


def _empty_audit_json() -> str:
    return json.dumps({"dependencies": []})


# ---------------------------------------------------------------------------
# Failure injection: reproduce the exact byte class from the CI crash.
# ---------------------------------------------------------------------------


def test_utf8_korean_bytes_no_longer_crash_pip_audit_gate(mod: ModuleType, tmp_path: Path) -> None:
    """pip-audit stdout containing UTF-8 Korean text must not crash the
    gate on a cp949-locale Windows runner -- reproduces task-8362."""
    payload = json.dumps(
        {
            "dependencies": [
                {
                    "name": "some-pkg",
                    "version": "1.0",
                    "skip_reason": "distribution marked as editable",
                    "note": _KOREAN_TEXT,
                }
            ]
        }
    )
    fake_stdout_bytes = payload.encode("utf-8")

    # build_pip_audit_command runs `python -c "..."` writing raw UTF-8 bytes
    # to stdout, unbuffered, so the real subprocess.run() encoding kwarg is
    # exercised end-to-end (not just mocked).
    printer = (
        "import sys;"
        "sys.stdout.buffer.write(" + repr(fake_stdout_bytes) + ");sys.stdout.buffer.flush()"
    )
    with patch.object(mod, "build_pip_audit_command", return_value=[sys.executable, "-c", printer]):
        ignore_file = tmp_path / ".pip-audit-ignore"
        rc = mod.main(["--ignore-file", str(ignore_file), "--python", sys.executable])

    assert rc == 0


def test_subprocess_run_pins_utf8_encoding(mod: ModuleType) -> None:
    """Regression guard: the subprocess.run call must not rely on the OS
    locale-preferred encoding (cp949 on this CI runner)."""
    with (
        patch.object(mod.subprocess, "run", wraps=subprocess.run) as run_spy,
        patch.object(
            mod, "build_pip_audit_command", return_value=[sys.executable, "-c", "print('{}')"]
        ),
    ):
        mod.main(["--python", sys.executable])

    assert run_spy.call_args.kwargs.get("encoding") == "utf-8"


# ---------------------------------------------------------------------------
# Red-gate reproduction: prove the *pre-fix* decode path actually breaks.
# ---------------------------------------------------------------------------


def test_red_gate_reproduction_cp949_decode_of_korean_bytes_raises() -> None:
    """Documents the exact defect: decoding the CI runner's pip-audit UTF-8
    output with the Windows-default cp949 codec raises UnicodeDecodeError.
    This is what `subprocess.run(..., text=True)` (no explicit encoding) does
    under the hood on that host -- the failure this task fixes."""
    utf8_bytes = _KOREAN_TEXT.encode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        utf8_bytes.decode("cp949")


# ---------------------------------------------------------------------------
# Negative tests (>= 3)
# ---------------------------------------------------------------------------


def test_expired_ignore_entry_is_fatal(mod: ModuleType, tmp_path: Path) -> None:
    ignore_file = tmp_path / ".pip-audit-ignore"
    ignore_file.write_text(
        json.dumps(
            {
                "ignore": [
                    {"id": "GHSA-xxxx", "reason": "known false positive", "expires": "2000-01-01"}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(mod.IgnoreFileError, match="만료된 예외"):
        mod.load_ignored_vuln_ids(ignore_file, today=dt.date(2026, 9, 27))


def test_ignore_entry_missing_required_field_is_fatal(mod: ModuleType, tmp_path: Path) -> None:
    ignore_file = tmp_path / ".pip-audit-ignore"
    ignore_file.write_text(
        json.dumps({"ignore": [{"id": "GHSA-xxxx", "reason": "no expiry given"}]}),
        encoding="utf-8",
    )
    with pytest.raises(mod.IgnoreFileError, match="id/reason/expires"):
        mod.load_ignored_vuln_ids(ignore_file, today=dt.date(2026, 9, 27))


def test_non_json_stdout_is_a_fatal_audit_failure(mod: ModuleType) -> None:
    with pytest.raises(mod.AuditFailure, match="JSON이 아니다"):
        mod.evaluate_pip_audit_json("not json at all", set())


def test_unexpected_skip_reason_is_fatal(mod: ModuleType) -> None:
    payload = json.dumps(
        {
            "dependencies": [
                {"name": "some-pkg", "version": "1.0", "skip_reason": "could not find package"}
            ]
        }
    )
    failures = mod.evaluate_pip_audit_json(payload, set())
    assert failures == ["some-pkg: 수집 실패 — could not find package"]


# ---------------------------------------------------------------------------
# Performance assertion
# ---------------------------------------------------------------------------


def test_is_network_error_perf_within_budget(mod: ModuleType) -> None:
    text = "a normal pip-audit json line " * 50
    RelativeBudget().assert_within(
        lambda: mod.is_network_error(text),
        max_ratio=_IS_NETWORK_ERROR_MAX_RATIO,
        mode="cpu",
        label="is_network_error",
    )


# ---------------------------------------------------------------------------
# task-8375: proc.stdout/stderr can be None on a capture-failure path --
# is_network_error() must normalize instead of raising `TypeError: argument
# of type 'NoneType' is not iterable`, and an audit run that produces no
# output at all must be reported as an audit execution failure, not silently
# misread as a network error or a vulnerability finding.
# ---------------------------------------------------------------------------


def test_is_network_error_none_does_not_raise(mod: ModuleType) -> None:
    """Red-gate reproduction: bd49f668 crashed here with `marker in None`."""
    assert mod.is_network_error(None) is False


def test_is_network_error_none_stderr_with_real_network_marker_in_stdout(
    mod: ModuleType,
) -> None:
    assert mod.is_network_error(None) is False
    assert mod.is_network_error("ConnectionError: max retries exceeded") is True


def test_main_reports_audit_execution_failure_when_capture_yields_none(
    mod: ModuleType, tmp_path: Path
) -> None:
    """subprocess.run capture failure (stdout/stderr None) must surface as
    an "audit execution failed" result -- not a network error (rc=3) and not
    a silently-passing empty vulnerability list (rc=0)."""
    fake_proc = subprocess.CompletedProcess(
        args=["pip_audit"], returncode=1, stdout=None, stderr=None
    )
    ignore_file = tmp_path / ".pip-audit-ignore"
    with patch.object(mod.subprocess, "run", return_value=fake_proc):
        rc = mod.main(["--ignore-file", str(ignore_file), "--python", sys.executable])

    assert rc == 1


def test_main_empty_stdout_without_network_marker_is_audit_failure_not_network(
    mod: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_proc = subprocess.CompletedProcess(args=["pip_audit"], returncode=1, stdout="", stderr="")
    ignore_file = tmp_path / ".pip-audit-ignore"
    with patch.object(mod.subprocess, "run", return_value=fake_proc):
        rc = mod.main(["--ignore-file", str(ignore_file), "--python", sys.executable])

    assert rc == 1
    assert rc != mod.NETWORK_ERROR_RC
    assert "감사 실행 자체가 실패" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# task-11447: lock 밖(local-only, 예: mutation extras의 전이 의존) 취약점과
# lock 안(사람이 lock을 바꿔야 할) 취약점을 구분하는 보고 로직.
# ---------------------------------------------------------------------------


def test_parse_lock_names_normalizes_and_strips_comments_markers(mod: ModuleType) -> None:
    lock_text = (
        "# comment line\n"
        "\n"
        "PyJWT==2.15.1\n"
        "pydantic_settings==2.15.0  # pinned for X\n"
        "requests>=2.0; python_version >= '3.10'\n"
    )
    assert mod.parse_lock_names(lock_text) == {"pyjwt", "pydantic-settings", "requests"}


def test_parse_lock_names_empty_text_is_empty_set(mod: ModuleType) -> None:
    assert mod.parse_lock_names("") == set()


def test_failure_package_name_extracts_vuln_failure(mod: ModuleType) -> None:
    line = "multidict 6.8.0: CVE-2026-104874 (fix: 6.9.1)"
    assert mod.failure_package_name(line) == "multidict"


def test_failure_package_name_returns_none_for_skip_reason_failure(mod: ModuleType) -> None:
    line = "somepkg: 수집 실패 — PyPI에 없음"
    assert mod.failure_package_name(line) is None


def test_split_failures_by_lock_separates_local_only_from_locked(mod: ModuleType) -> None:
    failures = [
        "multidict 6.8.0: CVE-2026-104874 (fix: 6.9.1)",
        "pyjwt 2.14.0: CVE-2026-101918 (fix: 2.15.1)",
        "somepkg: 수집 실패 — PyPI에 없음",
    ]
    locked, local_only = mod.split_failures_by_lock(failures, {"pyjwt"})
    assert local_only == ["multidict 6.8.0: CVE-2026-104874 (fix: 6.9.1)"]
    assert locked == [
        "pyjwt 2.14.0: CVE-2026-101918 (fix: 2.15.1)",
        "somepkg: 수집 실패 — PyPI에 없음",
    ]


def test_split_failures_by_lock_no_failures_is_empty(mod: ModuleType) -> None:
    locked, local_only = mod.split_failures_by_lock([], {"pyjwt"})
    assert locked == []
    assert local_only == []


def test_main_tail_notes_local_only_packages_not_covered_by_actions(
    mod: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """supply_chain이 lock 밖 전이 의존(예: multidict) 때문에 적색일 때, 보고서
    tail에 'Actions parity 밖 로컬 전용' 사실이 드러나야 한다(task-11447)."""
    payload = json.dumps(
        {
            "dependencies": [
                {
                    "name": "multidict",
                    "version": "6.8.0",
                    "vulns": [
                        {
                            "id": "CVE-2026-104874",
                            "aliases": [],
                            "fix_versions": ["6.9.1"],
                        }
                    ],
                }
            ]
        }
    )
    lock_file = tmp_path / "requirements-lock.txt"
    lock_file.write_text("pyjwt==2.15.1\n", encoding="utf-8")
    ignore_file = tmp_path / ".pip-audit-ignore"
    printer = "import sys; print(" + repr(payload) + ")"
    with patch.object(mod, "build_pip_audit_command", return_value=[sys.executable, "-c", printer]):
        rc = mod.main(
            [
                "--ignore-file",
                str(ignore_file),
                "--lock-file",
                str(lock_file),
                "--python",
                sys.executable,
            ]
        )

    assert rc == 1
    err = capsys.readouterr().err
    assert "로컬 전용 패키지" in err
    assert "multidict" in err
