"""propose_security_bumps: classification, injected installability, fail-closed."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[3] / "scripts" / "propose_security_bumps.py"
_spec = importlib.util.spec_from_file_location("propose_security_bumps", _PATH)
assert _spec and _spec.loader
psb = importlib.util.module_from_spec(_spec)
sys.modules["propose_security_bumps"] = psb
_spec.loader.exec_module(psb)


@pytest.mark.parametrize(
    ("current", "proposed", "expected"),
    [
        ("1.2.3", "1.2.5", psb.AUTO),
        ("1.2.3", "1.4.0", psb.AUTO),
        ("1.2.3", "2.0.0", psb.MANUAL),
        ("1.2.3", None, psb.EXCEPTION),
    ],
)
def test_classify_bump(current: str, proposed: str | None, expected: str) -> None:
    assert psb.classify_bump(current, proposed) == expected


def _audit(fix: list[str]) -> str:
    return json.dumps(
        {
            "dependencies": [
                {
                    "name": "foo",
                    "version": "1.2.3",
                    "vulns": [{"id": "CVE-1", "fix_versions": fix}],
                }
            ]
        }
    )


def test_picks_min_installable_skipping_uninstallable() -> None:
    calls: list[str] = []

    def check(_p: str, v: str) -> bool:
        calls.append(v)
        return v != "1.2.4"

    vulns = psb.parse_audit_json(_audit(["1.10.0", "1.2.4", "1.3.0"]))
    props = psb.build_proposals(vulns, check)
    assert props[0].proposed == "1.3.0"
    assert calls == ["1.2.4", "1.3.0"]


def test_not_installable_becomes_exception_with_30_day_draft() -> None:
    vulns = psb.parse_audit_json(_audit(["1.2.4"]))
    props = psb.build_proposals(vulns, lambda _p, _v: False)
    assert props[0].classification == psb.EXCEPTION
    draft = psb.draft_ignore_entries(props, dt.date(2026, 10, 28))
    assert draft[0]["id"] == "CVE-1"
    assert draft[0]["expires"] == "2026-11-27"


def test_no_fix_versions_is_exception() -> None:
    vulns = psb.parse_audit_json(_audit([]))
    props = psb.build_proposals(vulns, lambda _p, _v: True)
    assert props[0].classification == psb.EXCEPTION


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    f = tmp_path / "a.json"
    f.write_text("{not json", encoding="utf-8")
    assert psb.main([str(f)], is_installable=lambda _p, _v: True) == 2


def test_network_failure_is_not_a_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "a.json"
    f.write_text(_audit(["1.2.4"]), encoding="utf-8")

    def boom(_p: str, _v: str) -> bool:
        raise psb.NetworkCheckError("offline")

    assert psb.main([str(f)], is_installable=boom) == 2
    assert "fail-closed" in capsys.readouterr().err


def test_main_dry_run_prints_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "a.json"
    f.write_text(_audit(["2.0.0"]), encoding="utf-8")
    assert psb.main([str(f)], is_installable=lambda _p, _v: True) == 0
    out = capsys.readouterr().out
    assert "manual-review" in out
    assert "dry-run" in out
