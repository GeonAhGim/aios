"""CONSIST-1 명세추적 검사군 -- spec_leaf_untraced / spec_template_incomplete.
task-3725 CONSIST-1c로 check_consistency.py에서 분리(순수 이동, 판정 로직
변경 없음).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from scripts.consistency.common import Hit, _iter_py_files, _read_text_cached

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


_LEAF_TOKEN_SCAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,6}-\d+[A-Za-z]?(?![A-Za-z0-9])")


def _referenced_leaf_ids(leaf_ids: set[str], *blobs: str) -> set[str]:
    """leaf_ids 중 blob들 안에 온전한 토큰으로 등장하는 것들을 모아 반환한다.

    leaf마다 blob 전체를 다시 search하면 O(len(leaf_ids) * len(blob))이 되어
    tests/가 커질 때마다(DEEPEN 커밋 누적) check_consistency의 CI 120s 타임아웃에
    가까워진다(esc-ci-consistency). 이전 시도는 leaf_ids를 alternation
    하나로 묶어 blob을 leaf당 한 번씩이 아니라 전체 한 번만 순회했지만,
    leaf_ids가 수백 개로 늘면서(task-8041 기준 409개) alternation 자체의
    backtracking 비용이 blob 크기(17MB+)에 비례해 다시 타임아웃에 근접했다
    (409 leaf, 17MB blob에서 5.5s). 같은 형태(`PREFIX-123X`)를 갖는 토큰을
    범용 패턴 하나로 한 번만 추출한 뒤 leaf_ids와의 set 조회(O(1))로 걸러내면
    alternation 없이 O(len(blob))로 끝난다(같은 조건에서 0.3s로 축소).
    """
    if not leaf_ids:
        return set()
    found: set[str] = set()
    for blob in blobs:
        for m in _LEAF_TOKEN_SCAN_RE.finditer(blob):
            token = m.group(0)
            if token in leaf_ids:
                found.add(token)
    return found


def check_spec_leaf_traceability(root: Path) -> list[Hit]:
    specs_dir = root / "docs" / "specs"
    if not specs_dir.is_dir():
        return []
    leaf_ids = _collect_spec_leaf_ids(specs_dir)
    if not leaf_ids:
        return []
    blobs = []
    # "src" is already walked+read by wiring/contracts/time_money via the
    # shared caches -- reuse them instead of a second rglob+read pass
    # (task-8000: check_consistency.py's local CI 120s timeout).
    blobs.extend(_read_text_cached(path) for path in _iter_py_files(root, "src"))
    for sub in ("tests", "scripts"):
        base = root / sub
        if base.is_dir():
            for path in base.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                blobs.append(path.read_text(encoding="utf-8", errors="replace"))
    code_blob = "\n".join(blobs)
    commit_blob = _git_commit_subjects(root)
    referenced = _referenced_leaf_ids(leaf_ids, code_blob, commit_blob)
    return [(f"docs/specs#{leaf}", 0) for leaf in sorted(leaf_ids) if leaf not in referenced]


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
