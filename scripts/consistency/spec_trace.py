"""CONSIST-1 명세추적 검사군 -- spec_leaf_untraced / spec_template_incomplete.
task-3725 CONSIST-1c로 check_consistency.py에서 분리(순수 이동, 판정 로직
변경 없음).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from scripts.consistency.common import Hit

# ---------------------------------------------------------------------------
# 8. spec_leaf_untraced
# ---------------------------------------------------------------------------

_LEAF_ROW_RE = re.compile(
    r"^\|\s*([A-Za-z]{1,6}-\d+[A-Za-z]?(?:~[A-Za-z]{1,6}-\d+[A-Za-z]?)?)\s*\|"
)
_LEAF_TOKEN_RE = re.compile(r"^([A-Za-z]+)-(\d+)([A-Za-z]?)$")


def _expand_leaf_ids(token: str) -> list[str]:
    if "~" not in token:
        return [token]
    left, right = token.split("~", 1)
    m1, m2 = _LEAF_TOKEN_RE.match(left), _LEAF_TOKEN_RE.match(right)
    if not (m1 and m2 and m1.group(1) == m2.group(1) and not m1.group(3) and not m2.group(3)):
        return [token]
    prefix, width = m1.group(1), len(m1.group(2))
    return [
        f"{prefix}-{str(n).zfill(width)}" for n in range(int(m1.group(2)), int(m2.group(2)) + 1)
    ]


def _collect_spec_leaf_ids(specs_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in sorted(specs_dir.glob("L4_*.md")):
        in_status_block = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("<!-- spec-status:begin"):
                in_status_block = True
                continue
            if stripped.startswith("<!-- spec-status:end"):
                in_status_block = False
                continue
            if in_status_block:
                continue
            m = _LEAF_ROW_RE.match(stripped)
            if m:
                ids.update(_expand_leaf_ids(m.group(1)))
    return ids


def _git_commit_subjects(root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "log", "--format=%s"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        return (r.stdout or "") if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _leaf_referenced(leaf: str, *blobs: str) -> bool:
    """leaf가 blob 안에 온전한 토큰으로 등장하는지 검사한다.

    plain substring(`leaf in blob`)은 AI-2가 AI-22/AI-20의 접두라서 그 커밋/코드만
    보고도 "추적됨"으로 오판한다 -- 양옆이 영숫자가 아닐 때만 일치로 센다.
    """
    pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(leaf) + r"(?![A-Za-z0-9])")
    return any(pattern.search(blob) for blob in blobs)


def check_spec_leaf_traceability(root: Path) -> list[Hit]:
    specs_dir = root / "docs" / "specs"
    if not specs_dir.is_dir():
        return []
    leaf_ids = _collect_spec_leaf_ids(specs_dir)
    if not leaf_ids:
        return []
    blobs = []
    for sub in ("src", "tests", "scripts"):
        base = root / sub
        if base.is_dir():
            for path in base.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                blobs.append(path.read_text(encoding="utf-8", errors="replace"))
    code_blob = "\n".join(blobs)
    commit_blob = _git_commit_subjects(root)
    return [
        (f"docs/specs#{leaf}", 0)
        for leaf in sorted(leaf_ids)
        if not _leaf_referenced(leaf, code_blob, commit_blob)
    ]


# ---------------------------------------------------------------------------
# 12. spec_template_incomplete
# ---------------------------------------------------------------------------

_LEAF_SECTION_RE = re.compile(r"^##\s*9\.", re.MULTILINE)
_OPEN_SECTION_RE = re.compile(r"^##\s*10\.", re.MULTILINE)


def check_spec_template(root: Path) -> list[Hit]:
    specs_dir = root / "docs" / "specs"
    if not specs_dir.is_dir():
        return []
    hits: list[Hit] = []
    for path in sorted(specs_dir.glob("L4_*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        if not _LEAF_SECTION_RE.search(text):
            hits.append((rel, 0))
        if not _OPEN_SECTION_RE.search(text) and "미확정" not in text:
            hits.append((rel, 0))
    return hits
