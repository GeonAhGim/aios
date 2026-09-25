"""ADR-2026-09-09-F: docs/ADR_INDEX.md generator.

Parses each `docs/design/ADR-*.md` file's title line, `## Status` section, and
Supersedes/Amended markers into a single table (`docs/ADR_INDEX.md`). The parser is a
pure function (`parse_adr_text`) so tests do not need a real repo checkout — the I/O
(reading files, writing the index) lives only in `collect_adrs`/`main`.

Usage:
  python scripts/gen_adr_index.py            # (re)generate docs/ADR_INDEX.md
  python scripts/gen_adr_index.py --check    # exit 2 if the committed file is stale
    (this is the `adr_index` step wired into fleet ci_recheck.build_steps)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]

# First non-empty line of the file: "# ADR-2026-08-10-D: <title>".
_TITLE_LINE_RE = re.compile(r"^#\s+(ADR-\d{4}-\d{2}-\d{2}(?:-[A-Z])?)\s*:?\s*(.*)$")
# Body of the "## Status" section, up to the next "## " heading or end of file.
_STATUS_SECTION_RE = re.compile(r"^##\s+Status\s*\n(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
_STATUS_WORD_RE = re.compile(r"\b(Accepted|Proposed|Draft|Rejected|Deprecated|Superseded)\b")
# Status lines always carry their own date in parentheses, e.g. "(2026-08-14)" — prefer
# that over a bare date, since a "Superseded by ADR-2026-08-10-D-..." reference embeds
# an unrelated date (the superseding ADR's own id) earlier in the same line.
_STATUS_DATE_PAREN_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})")
_STATUS_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_SUPERSEDED_BY_RE = re.compile(r"Superseded by ([A-Za-z0-9_.\-]+)")
# "ADR-2026-08-10을 대체(Supersedes)한다" — the referenced id comes *before* the Korean
# verb, unlike the English "Supersedes: ADR-..." form.
_SUPERSEDES_KO_RE = re.compile(
    r"(ADR-\d{4}-\d{2}-\d{2}(?:-[A-Z])?)\S*\s*(?:을|를)\s*대체\(?Supersedes\)?", re.IGNORECASE
)
_SUPERSEDES_EN_RE = re.compile(r"Supersedes:?\s*(ADR-\d{4}-\d{2}-\d{2}(?:-[A-Z])?)", re.IGNORECASE)
# Amendments show up either as a heading ("## Amended 2026-09-09 (...)") or as bold
# inline text right inside the Status section ("**Amended 2026-09-04 (같은 날, ...)**").
_AMENDED_RE = re.compile(r"(?:^##\s+Amended|\*\*Amended)\s*\(?(\d{4}-\d{2}-\d{2})", re.MULTILINE)


class AdrRecord(TypedDict):
    adr_id: str
    title: str
    filename: str
    status: str | None
    status_date: str | None
    superseded_by: str | None
    supersedes: tuple[str, ...]
    amended_dates: tuple[str, ...]
    status_missing: bool


def parse_adr_text(text: str) -> AdrRecord:
    """Pure parse of one ADR file's contents. `filename` is filled in by the caller
    (`collect_adrs`) since the id/title live inside the text, not the path."""
    first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
    title_match = _TITLE_LINE_RE.match(first_line.strip())
    adr_id = title_match.group(1) if title_match else ""
    title = title_match.group(2).strip() if title_match else ""

    status_section = _STATUS_SECTION_RE.search(text)
    status: str | None = None
    status_date: str | None = None
    superseded_by: str | None = None
    if status_section:
        block = status_section.group(1)
        first_status_line = next((ln for ln in block.splitlines() if ln.strip()), "")
        word_match = _STATUS_WORD_RE.search(first_status_line)
        status = word_match.group(1) if word_match else None
        date_match = _STATUS_DATE_PAREN_RE.search(first_status_line) or _STATUS_DATE_RE.search(
            first_status_line
        )
        status_date = date_match.group(1) if date_match else None
        superseded_by_match = _SUPERSEDED_BY_RE.search(first_status_line)
        if superseded_by_match:
            superseded_by = superseded_by_match.group(1).removesuffix(".md")

    supersedes = tuple(
        sorted(set(_SUPERSEDES_KO_RE.findall(text)) | set(_SUPERSEDES_EN_RE.findall(text)))
    )
    amended_dates = tuple(sorted(set(_AMENDED_RE.findall(text))))

    return {
        "adr_id": adr_id,
        "title": title,
        "filename": "",
        "status": status,
        "status_date": status_date,
        "superseded_by": superseded_by,
        "supersedes": supersedes,
        "amended_dates": amended_dates,
        # Missing = no "## Status" heading, or a Status heading with no recognizable
        # status word — either way the table cannot show a real status for this ADR.
        "status_missing": status is None,
    }


def collect_adrs(design_dir: Path) -> list[AdrRecord]:
    records: list[AdrRecord] = []
    for path in sorted(design_dir.glob("ADR-*.md")):
        record = parse_adr_text(path.read_text(encoding="utf-8"))
        record["filename"] = path.name
        records.append(record)
    records.sort(key=lambda r: r["adr_id"] or r["filename"])
    return records


def render_table(records: list[AdrRecord]) -> str:
    header = "| ID | Title | Status | Date | Supersedes | Superseded By | Amended | File |\n"
    sep = "|---|---|---|---|---|---|---|---|\n"
    row_fmt = (
        "| {id} | {title} | {status} | {date} | {supersedes} | {superseded_by} | "
        "{amended} | {file} |"
    )
    rows = [
        row_fmt.format(
            id=r["adr_id"] or "?",
            title=r["title"].replace("|", "\\|"),
            status=r["status"] or "**MISSING**",
            date=r["status_date"] or "",
            supersedes=", ".join(r["supersedes"]),
            superseded_by=r["superseded_by"] or "",
            amended=", ".join(r["amended_dates"]),
            file=r["filename"],
        )
        for r in records
    ]
    return header + sep + "\n".join(rows) + "\n"


def generate_index_text(records: list[AdrRecord]) -> str:
    missing = [r["adr_id"] or r["filename"] for r in records if r["status_missing"]]
    missing_line = (
        f"상태 없음(`## Status` 절 없음 또는 파싱 불가) {len(missing)}건: {', '.join(missing)}."
        if missing
        else "상태 없음 0건."
    )
    return (
        "# ADR Index\n\n"
        "이 문서는 `scripts/gen_adr_index.py`가 `docs/design/ADR-*.md`에서 자동 생성한다"
        "(ADR-2026-09-09-F). 직접 수정하지 말 것 — 스크립트를 다시 실행해 갱신하고 커밋한다.\n\n"
        f"전체 {len(records)}건. {missing_line}\n\n"
        f"{render_table(records)}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="docs/ADR_INDEX.md generator/checker (ADR-2026-09-09-F)"
    )
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 2 if docs/ADR_INDEX.md does not match the freshly generated content",
    )
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    design_dir = args.repo / "docs" / "design"
    out_path = args.repo / "docs" / "ADR_INDEX.md"
    records = collect_adrs(design_dir)
    text = generate_index_text(records)

    if args.check:
        current = out_path.read_text(encoding="utf-8") if out_path.exists() else None
        if current != text:
            print(
                "FAIL: docs/ADR_INDEX.md가 최신 ADR 파일 상태와 다르다. "
                "`python scripts/gen_adr_index.py`로 재생성해 커밋하라."
            )
            return 2
        print(f"OK: docs/ADR_INDEX.md 최신 ({len(records)}건)")
        return 0

    out_path.write_text(text, encoding="utf-8")
    print(f"generated docs/ADR_INDEX.md ({len(records)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
