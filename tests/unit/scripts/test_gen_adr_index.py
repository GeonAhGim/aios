"""scripts/gen_adr_index.py unit tests (ADR-2026-09-09-F, task-2745).

DoD: parser unit tests (>=3 cases), a missing-status negative case, a red-gate
reproduction (`--check` exit 2 on drift), one failure-injection case (missing
output file), and one throughput assertion for collect_adrs/render over a
synthetic corpus (this script has no entry in the ADR-2026-09-09-C per-axis
performance budget table, so the budget below is this leaf's own — chosen well
above the corpus this repo actually has, ~35 files, so it stays meaningful as
the corpus grows).
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gen_adr_index = _load_module("gen_adr_index", SCRIPTS_DIR / "gen_adr_index.py")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------- parse_adr_text


def test_parse_adr_text_standard_accepted():
    text = (
        "# ADR-2026-08-10-B: 기술 스택 확정\n\n"
        "## Status\nAccepted (2026-08-10, 사용자 승인)\n\n"
        "## Context\n본문.\n"
    )
    r = gen_adr_index.parse_adr_text(text)
    assert r["adr_id"] == "ADR-2026-08-10-B"
    assert r["title"] == "기술 스택 확정"
    assert r["status"] == "Accepted"
    assert r["status_date"] == "2026-08-10"
    assert r["superseded_by"] is None
    assert r["supersedes"] == ()
    assert r["status_missing"] is False


def test_parse_adr_text_accepted_with_supersedes():
    text = (
        "# ADR-2026-08-10-D: 정식 확정\n\n"
        "## Status\n**Accepted (2026-08-14, 정식 확정)** — ADR-2026-08-10을 대체(Supersedes)한다.\n"
        "본문 계속.\n\n"
        "## Context\n...\n"
    )
    r = gen_adr_index.parse_adr_text(text)
    assert r["status"] == "Accepted"
    assert r["status_date"] == "2026-08-14"
    assert r["supersedes"] == ("ADR-2026-08-10",)


def test_parse_adr_text_superseded_by():
    text = (
        "# ADR-2026-08-10: 임시 운영 방식\n\n"
        "## Status\n**Superseded by ADR-2026-08-10-D-platform-approval-finalized.md**"
        " (2026-08-14) —\n"
        "역사적 기록으로만 보존.\n\n"
        "~~Accepted (2026-08-10, 사용자 승인)~~ → Superseded\n\n"
        "## Context\n...\n"
    )
    r = gen_adr_index.parse_adr_text(text)
    assert r["status"] == "Superseded"
    assert r["status_date"] == "2026-08-14"
    assert r["superseded_by"] == "ADR-2026-08-10-D-platform-approval-finalized"


def test_parse_adr_text_amended_dates_heading_and_bold_forms():
    text = (
        "# ADR-2026-09-04-D: MVP-1 T3 범위\n\n"
        "## Status\nAccepted (2026-09-04, Chief Architect).\n"
        "**Amended 2026-09-04 (같은 날, 지시)**: 표 갱신.\n\n"
        "## Amended 2026-09-09 (ADR-2026-09-09-B/C/F)\n본문.\n"
    )
    r = gen_adr_index.parse_adr_text(text)
    assert r["amended_dates"] == ("2026-09-04", "2026-09-09")


def test_parse_adr_text_missing_status_section_is_flagged():
    """Negative case: no `## Status` heading at all (matches the two real ADR files
    that use a `> 상태:` blockquote instead — those cannot be table-parsed either)."""
    text = "# ADR-2026-08-28: 다자산군 지원 확장\n\n> 상태: 확정(구조 원칙)\n\n## 배경\n...\n"
    r = gen_adr_index.parse_adr_text(text)
    assert r["status"] is None
    assert r["status_missing"] is True


def test_parse_adr_text_status_heading_without_recognizable_word_is_missing():
    """Negative case: a `## Status` section exists but its content has no recognized
    status word — still counts as missing, not silently defaulted to some status."""
    text = "# ADR-2026-01-01-Z: 알수없음\n\n## Status\n검토 중, 미확정.\n\n## Context\n...\n"
    r = gen_adr_index.parse_adr_text(text)
    assert r["status"] is None
    assert r["status_missing"] is True


# --------------------------------------------------------------------- collect_adrs / render


def test_collect_adrs_sorts_by_id(tmp_path):
    design = tmp_path / "docs" / "design"
    _write(
        design / "ADR-2026-09-01-B-b.md",
        "# ADR-2026-09-01-B: B\n\n## Status\nAccepted (2026-09-01)\n",
    )
    _write(
        design / "ADR-2026-08-01-A-a.md",
        "# ADR-2026-08-01-A: A\n\n## Status\nAccepted (2026-08-01)\n",
    )

    records = gen_adr_index.collect_adrs(design)

    assert [r["adr_id"] for r in records] == ["ADR-2026-08-01-A", "ADR-2026-09-01-B"]


def test_render_table_marks_missing_status():
    records = [
        gen_adr_index.parse_adr_text("# ADR-2026-01-01-A: X\n\n## Status\nAccepted (2026-01-01)\n"),
    ]
    records[0]["filename"] = "ADR-2026-01-01-A-x.md"
    table = gen_adr_index.render_table(records)
    assert "Accepted" in table

    missing = gen_adr_index.parse_adr_text("# ADR-2026-01-02-B: Y\n\n> 상태: ?\n")
    missing["filename"] = "ADR-2026-01-02-B-y.md"
    table2 = gen_adr_index.render_table([missing])
    assert "**MISSING**" in table2


# --------------------------------------------------------------------- main() CLI / --check gate


def test_main_generates_index_file(tmp_path):
    design = tmp_path / "docs" / "design"
    _write(
        design / "ADR-2026-01-01-A-a.md",
        "# ADR-2026-01-01-A: A\n\n## Status\nAccepted (2026-01-01)\n",
    )

    rc = gen_adr_index.main(["--repo", str(tmp_path)])

    assert rc == 0
    out = (tmp_path / "docs" / "ADR_INDEX.md").read_text(encoding="utf-8")
    assert "ADR-2026-01-01-A" in out
    assert "전체 1건" in out


def test_main_check_passes_when_index_is_current(tmp_path):
    design = tmp_path / "docs" / "design"
    _write(
        design / "ADR-2026-01-01-A-a.md",
        "# ADR-2026-01-01-A: A\n\n## Status\nAccepted (2026-01-01)\n",
    )
    assert gen_adr_index.main(["--repo", str(tmp_path)]) == 0

    rc = gen_adr_index.main(["--repo", str(tmp_path), "--check"])

    assert rc == 0


def test_main_check_fails_exit2_on_drift(tmp_path, capsys):
    """Red-gate reproduction: docs/ADR_INDEX.md is generated once, then a new ADR file
    is added without regenerating the index — --check must fail closed with exit 2
    (this is exactly the ci_recheck `adr_index` step's failure mode)."""
    design = tmp_path / "docs" / "design"
    _write(
        design / "ADR-2026-01-01-A-a.md",
        "# ADR-2026-01-01-A: A\n\n## Status\nAccepted (2026-01-01)\n",
    )
    assert gen_adr_index.main(["--repo", str(tmp_path)]) == 0

    _write(
        design / "ADR-2026-01-02-B-b.md",
        "# ADR-2026-01-02-B: B\n\n## Status\nAccepted (2026-01-02)\n",
    )
    capsys.readouterr()

    rc = gen_adr_index.main(["--repo", str(tmp_path), "--check"])

    assert rc == 2
    assert "FAIL" in capsys.readouterr().out


def test_main_check_fails_when_index_file_missing(tmp_path):
    """Failure injection: docs/ADR_INDEX.md was never generated (e.g. deleted by hand)
    — --check must fail closed (exit 2), not crash or silently pass."""
    design = tmp_path / "docs" / "design"
    _write(
        design / "ADR-2026-01-01-A-a.md",
        "# ADR-2026-01-01-A: A\n\n## Status\nAccepted (2026-01-01)\n",
    )

    rc = gen_adr_index.main(["--repo", str(tmp_path), "--check"])

    assert rc == 2


@pytest.mark.perf
def test_collect_and_render_throughput_budget(tmp_path):
    """Numeric performance assertion: this repo currently has ~35 ADR files; assert
    collect_adrs+render_table stays well under budget for a corpus an order of
    magnitude larger (300 files) so the gate stays cheap as the corpus grows."""
    design = tmp_path / "docs" / "design"
    for i in range(300):
        _write(
            design / f"ADR-2026-01-{i % 28 + 1:02d}-{chr(65 + i % 26)}-x{i}.md",
            f"# ADR-2026-01-{i % 28 + 1:02d}-{chr(65 + i % 26)}: 제목{i}\n\n"
            f"## Status\nAccepted (2026-01-{i % 28 + 1:02d})\n\n## Context\n본문\n",
        )

    start = time.perf_counter()
    records = gen_adr_index.collect_adrs(design)
    gen_adr_index.render_table(records)
    elapsed = time.perf_counter() - start

    assert len(records) == 300
    assert elapsed < 2.0
