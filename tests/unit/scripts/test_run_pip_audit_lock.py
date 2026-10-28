"""scripts/run_pip_audit.py lock-file auditing tests -- task-20774.

Lock 파일 자체도 감사하게 확장한 후의 동작을 검증한다:
(1) lock에만 취약 핀이 있으면 실패
(2) 예외 파일에 유효 항목이 있으면 통과
(3) lock 없음 -> 건너뜀
네트워크는 모킹.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


def _load_module() -> ModuleType:
    import importlib.util

    ROOT = Path(__file__).resolve().parents[3]
    SCRIPT_PATH = ROOT / "scripts" / "run_pip_audit.py"
    spec = importlib.util.spec_from_file_location("run_pip_audit_lock", SCRIPT_PATH)
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
# (1) lock에만 취약 핀이 있으면 실패
# ---------------------------------------------------------------------------


def test_lock_only_vulnerability_causes_failure(mod: ModuleType, tmp_path: Path) -> None:
    """환경에는 취약점이 없고 lock 파일에만 CVE가 있으면 게이트는 실패해야 한다."""
    # 환경 감사: 깨끗
    env_stdout = _empty_audit_json()
    # lock 감사: filelock에 CVE 있음
    lock_payload = json.dumps(
        {
            "dependencies": [
                {
                    "name": "filelock",
                    "version": "4.1.0",
                    "vulns": [
                        {
                            "id": "CVE-2026-99999",
                            "aliases": [],
                            "fix_versions": ["4.2.0"],
                        }
                    ],
                }
            ]
        }
    )

    def _run_side_effect(cmd, **kwargs):
        # lock 명령인지 환경 명령인지 확인: -r이 있으면 lock
        if any(str(c).endswith("requirements-lock.txt") for c in cmd):
            result_type = "lock"
        else:
            result_type = "env"
        from subprocess import CompletedProcess

        if result_type == "lock":
            return CompletedProcess(args=cmd, returncode=0, stdout=lock_payload, stderr="")
        return CompletedProcess(args=cmd, returncode=0, stdout=env_stdout, stderr="")

    ignore_file = tmp_path / ".pip-audit-ignore"
    lock_file = tmp_path / "requirements-lock.txt"
    lock_file.write_text("filelock==4.1.0\n", encoding="utf-8")

    with (
        patch("subprocess.run", side_effect=_run_side_effect),
    ):
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


# ---------------------------------------------------------------------------
# (2) 예외 파일에 유효 항목이 있으면 통과
# ---------------------------------------------------------------------------


def test_valid_ignore_in_ignore_file_allows_lock_vuln(mod: ModuleType, tmp_path: Path) -> None:
    """lock의 CVE가 .pip-audit-ignore에 유효하게 등록되면 통과해야 한다."""
    env_stdout = _empty_audit_json()
    lock_payload = json.dumps(
        {
            "dependencies": [
                {
                    "name": "filelock",
                    "version": "4.1.0",
                    "vulns": [
                        {
                            "id": "CVE-2026-99999",
                            "aliases": [],
                            "fix_versions": ["4.2.0"],
                        }
                    ],
                }
            ]
        }
    )

    ignore_file = tmp_path / ".pip-audit-ignore"
    ignore_file.write_text(
        json.dumps(
            {
                "ignore": [
                    {
                        "id": "CVE-2026-99999",
                        "reason": "false positive in lock audit",
                        "expires": "2027-12-31",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    lock_file = tmp_path / "requirements-lock.txt"
    lock_file.write_text("filelock==4.1.0\n", encoding="utf-8")

    def _run_side_effect(cmd, **kwargs):
        from subprocess import CompletedProcess

        if any(str(c).endswith("requirements-lock.txt") for c in cmd):
            return CompletedProcess(args=cmd, returncode=0, stdout=lock_payload, stderr="")
        return CompletedProcess(args=cmd, returncode=0, stdout=env_stdout, stderr="")

    with patch("subprocess.run", side_effect=_run_side_effect):
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

    assert rc == 0


# ---------------------------------------------------------------------------
# (3) lock 파일이 없으면 건너뜀
# ---------------------------------------------------------------------------


def test_missing_lock_file_skips_lock_audit(mod: ModuleType, tmp_path: Path) -> None:
    """lock 파일이 없으면 lock 감사를 건너뛰고 환경만 감사해야 한다."""
    env_stdout = _empty_audit_json()
    ignore_file = tmp_path / ".pip-audit-ignore"
    lock_file = tmp_path / "requirements-lock.txt"
    # lock 파일을 만들지 않는다 -> 존재하지 않음

    def _run_side_effect(cmd, **kwargs):
        from subprocess import CompletedProcess

        # lock 명령이 호출되면 실패 — lock 감사가 호출되면 안 됨
        if any(str(c).endswith("requirements-lock.txt") for c in cmd):
            raise RuntimeError("lock audit should not run when lock file is missing")
        return CompletedProcess(args=cmd, returncode=0, stdout=env_stdout, stderr="")

    with patch("subprocess.run", side_effect=_run_side_effect):
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

    assert rc == 0


def test_env_only_vulnerability_still_fails_with_lock_present(
    mod: ModuleType, tmp_path: Path
) -> None:
    """환경에 취약점이 있고 lock 파일이 있어도 환경 취약점은 여전히 실패해야 한다."""
    env_payload = json.dumps(
        {
            "dependencies": [
                {
                    "name": "some-env-pkg",
                    "version": "1.0.0",
                    "vulns": [
                        {
                            "id": "CVE-2026-11111",
                            "aliases": [],
                            "fix_versions": ["1.1.0"],
                        }
                    ],
                }
            ]
        }
    )
    lock_payload = _empty_audit_json()

    def _run_side_effect(cmd, **kwargs):
        from subprocess import CompletedProcess

        if any(str(c).endswith("requirements-lock.txt") for c in cmd):
            return CompletedProcess(args=cmd, returncode=0, stdout=lock_payload, stderr="")
        return CompletedProcess(args=cmd, returncode=0, stdout=env_payload, stderr="")

    ignore_file = tmp_path / ".pip-audit-ignore"
    lock_file = tmp_path / "requirements-lock.txt"
    lock_file.write_text("other-pkg==1.0.0\n", encoding="utf-8")

    with patch("subprocess.run", side_effect=_run_side_effect):
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
