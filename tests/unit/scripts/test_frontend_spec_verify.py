"""scripts/frontend_spec/verify_spec.py -- the anti-hallucination gate for frontend specs."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.frontend_spec import verify_spec as v


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "docs/frontend").mkdir(parents=True)
    (tmp_path / "docs/frontend/SCREEN_SPEC_TEMPLATE.md").write_text(
        "## 1. Overview\n## 2. Data\n", encoding="utf-8"
    )
    src = tmp_path / "web" / "Page.tsx"
    src.parent.mkdir()
    src.write_text(
        "\n".join(f"line {i}" for i in range(1, 11)) + "\nconst strategyId = 1\n", "utf-8"
    )
    other = tmp_path / "a" / "dup.py"
    other.parent.mkdir()
    other.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "dup.py").write_text("y = 2\n", encoding="utf-8")
    monkeypatch.setattr(v, "ROOT", tmp_path)
    monkeypatch.setattr(v, "_index", None)
    monkeypatch.setattr(v, "_file_cache", {})
    return tmp_path


def _doc(repo: Path, body: str, name: str = "docs/frontend/screens/S.md") -> Path:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def kinds(report: v.Report) -> list[str]:
    return [f.kind for f in report.findings]


def test_correct_row_passes(repo: Path) -> None:
    doc = _doc(repo, "## 1. Overview\n## 2. Data\n| a | `strategyId` | `web/Page.tsx:11` |\n")
    report = v.verify_file(doc, require_sections=True)
    assert report.findings == [] and report.citations == 1 and report.identifiers == 1


def test_invented_identifier_is_reported(repo: Path) -> None:
    doc = _doc(repo, "| field | `order_type` | `web/Page.tsx:3` |\n")
    report = v.verify_file(doc)
    assert kinds(report) == ["missing_identifier"]
    assert report.findings[0].detail == "order_type"


def test_line_outside_file_is_reported(repo: Path) -> None:
    doc = _doc(repo, "| x | `web/Page.tsx:99` |\n| y | `web/Page.tsx:5-3` |\n")
    assert kinds(v.verify_file(doc)) == ["bad_line", "bad_line"]


def test_missing_and_ambiguous_files_are_distinguished(repo: Path) -> None:
    doc = _doc(repo, "| x | `nope.tsx:1` |\n| y | `dup.py:1` |\n")
    assert kinds(v.verify_file(doc)) == ["missing_file", "ambiguous_file"]


def test_fenced_code_blocks_are_not_checked(repo: Path) -> None:
    doc = _doc(repo, "```\n| x | `ghost_name` | `web/Page.tsx:999` |\n```\n")
    assert v.verify_file(doc).findings == []


def test_rows_without_citations_are_not_claims(repo: Path) -> None:
    doc = _doc(repo, "| x | `whatever_identifier` |\n")
    report = v.verify_file(doc)
    assert report.findings == [] and report.citations == 0


def test_missing_template_section_is_reported_for_screens(repo: Path) -> None:
    doc = _doc(repo, "## 1. Overview\n")
    assert kinds(v.verify_file(doc, require_sections=True)) == ["missing_section"]


def test_generic_and_short_identifiers_prove_nothing(repo: Path) -> None:
    doc = _doc(repo, "| x | `data` | `id` | `loading` | `web/Page.tsx:1` |\n")
    assert v.verify_file(doc).identifiers == 0


def test_baseline_ratchet_blocks_regressions(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _doc(repo, "| f | `ghost_field` | `web/Page.tsx:1` |\n")
    rel = doc.relative_to(repo).as_posix()
    baseline = repo / "docs/frontend/.spec_verify_baseline.json"
    monkeypatch.setattr(v, "BASELINE", baseline)
    assert v.main([rel, "--update"]) == 0
    assert v.main([rel, "--check"]) == 0
    doc.write_text("| f | `ghost_field` `ghost_other` | `web/Page.tsx:1` |\n", encoding="utf-8")
    monkeypatch.setattr(v, "_file_cache", {})
    assert v.main([rel, "--check"]) == 1
    assert "above baseline" in capsys.readouterr().err
