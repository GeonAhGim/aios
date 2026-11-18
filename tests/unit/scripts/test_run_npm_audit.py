"""scripts/run_npm_audit.py 단위 테스트 -- 프런트엔드 공급망 게이트(H-5)의 예외 규칙.

npm audit --json 형태의 보고서를 직접 만들어 판정만 검증한다(네트워크·npm 불필요).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[3] / "scripts" / "run_npm_audit.py"
GHSA = "GHSA-fc5q-39xc-vjpf"
OTHER = "GHSA-aaaa-bbbb-cccc"
TODAY = dt.date(2026, 11, 18)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_npm_audit_under_test", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


def _via(ghsa: str, name: str = "picomatch", severity: str = "high") -> dict[str, Any]:
    return {
        "source": 1,
        "name": name,
        "title": f"{name} advisory",
        "url": f"https://github.com/advisories/{ghsa}",
        "severity": severity,
        "range": "<=4.0.7",
    }


def _report(*advisories: dict[str, Any], fix: object = None) -> dict[str, Any]:
    vulns: dict[str, Any] = {}
    for via in advisories:
        vulns[via["name"]] = {
            "name": via["name"],
            "severity": via["severity"],
            "via": [via],
            "fixAvailable": fix if fix is not None else {"name": "vite", "isSemVerMajor": True},
        }
    # 파생 취약점(via가 문자열) -- advisory로 세면 안 된다.
    vulns["vite"] = {"name": "vite", "severity": "high", "via": ["picomatch"], "fixAvailable": True}
    return {"vulnerabilities": vulns}


def _write_ignore(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"ignore": entries}), encoding="utf-8")
    return path


def _entry(ghsa: str = GHSA, expires: str = "2026-12-02") -> dict[str, Any]:
    return {"id": ghsa, "reason": "패치판 없음", "expires": expires}


def test_blocking_advisory_without_exception_fails() -> None:
    failures = mod.evaluate(_report(_via(GHSA)), {})
    assert len(failures) == 1
    assert GHSA.lower() in failures[0]


def test_listed_advisory_passes_and_derived_nodes_are_not_counted() -> None:
    report = _report(_via(GHSA))
    assert mod.evaluate(report, {GHSA.lower(): "사유"}) == []
    assert list(mod.collect_blocking(report)) == [GHSA.lower()]


def test_other_high_advisory_still_fails_even_when_one_is_excepted() -> None:
    report = _report(_via(GHSA), _via(OTHER, name="lodash"))
    failures = mod.evaluate(report, {GHSA.lower(): "사유"})
    assert len(failures) == 1
    assert OTHER.lower() in failures[0]


def test_moderate_advisory_is_below_threshold() -> None:
    assert mod.evaluate(_report(_via(OTHER, name="vitest", severity="moderate")), {}) == []


def test_stale_exception_fails_when_advisory_disappeared() -> None:
    failures = mod.evaluate({"vulnerabilities": {}}, {GHSA.lower(): "사유"})
    assert len(failures) == 1
    assert "쓸모없어진 예외" in failures[0]


def test_exception_fails_when_compatible_patch_became_available() -> None:
    report = _report(_via(GHSA), fix=True)
    failures = mod.evaluate(report, {GHSA.lower(): "사유"})
    assert len(failures) == 1
    assert "패치" in failures[0]


def test_expired_exception_raises(tmp_path: Path) -> None:
    ignore = _write_ignore(tmp_path / ".npm-audit-ignore", [_entry(expires="2026-11-17")])
    with pytest.raises(mod.IgnoreFileError, match="만료"):
        mod.load_ignored_ids(ignore, today=TODAY)


def test_exception_missing_reason_or_bad_id_raises(tmp_path: Path) -> None:
    path = tmp_path / ".npm-audit-ignore"
    _write_ignore(path, [{"id": GHSA, "expires": "2026-12-02"}])
    with pytest.raises(mod.IgnoreFileError, match="reason"):
        mod.load_ignored_ids(path, today=TODAY)
    _write_ignore(path, [_entry(ghsa="not-a-ghsa")])
    with pytest.raises(mod.IgnoreFileError, match="GHSA"):
        mod.load_ignored_ids(path, today=TODAY)
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(mod.IgnoreFileError, match="JSON"):
        mod.load_ignored_ids(path, today=TODAY)


def test_valid_exception_loaded_and_missing_file_is_empty(tmp_path: Path) -> None:
    ignore = _write_ignore(tmp_path / ".npm-audit-ignore", [_entry()])
    assert set(mod.load_ignored_ids(ignore, today=TODAY)) == {GHSA.lower()}
    assert mod.load_ignored_ids(tmp_path / "none", today=TODAY) == {}


def test_main_reports_failure_for_unparseable_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mod, "run_npm_audit", lambda _d: (1, "not json at all"))
    assert mod.main(["--frontend-dir", str(tmp_path)]) == 2


def test_main_distinguishes_network_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mod, "run_npm_audit", lambda _d: (1, "npm ERR! code ENOTFOUND registry"))
    assert mod.main(["--frontend-dir", str(tmp_path)]) == mod.NETWORK_ERROR_RC


def test_main_passes_with_valid_exception_and_fails_without(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mod, "run_npm_audit", lambda _d: (1, json.dumps(_report(_via(GHSA)))))
    assert mod.main(["--frontend-dir", str(tmp_path)]) == 1
    _write_ignore(tmp_path / ".npm-audit-ignore", [_entry(expires="2999-01-01")])
    assert mod.main(["--frontend-dir", str(tmp_path)]) == 0


def test_repo_exception_file_is_valid_and_unexpired() -> None:
    ignore = _PATH.parents[1] / "frontend" / ".npm-audit-ignore"
    ids = mod.load_ignored_ids(ignore, today=dt.date.today())
    assert GHSA.lower() in ids


def test_evaluate_is_fast_on_large_reports() -> None:
    advisories = [_via(f"GHSA-{i:04x}-aaaa-bbbb", name=f"pkg{i}") for i in range(2000)]
    started = time.perf_counter()
    mod.evaluate(_report(*advisories), {})
    assert time.perf_counter() - started < 2.0
