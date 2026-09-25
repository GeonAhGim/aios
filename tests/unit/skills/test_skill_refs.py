"""`.claude/skills/review-*/SKILL.md` 근거 링크 검사 -- task-3061 SK-1.

ADR-2026-09-10-A Decision 2가 요구하는 8종 영역별 검토 스킬의 구조를 검증한다:
frontmatter가 review_plan(OPS-35)이 로드할 paths/tier/axis를 갖고, 체크리스트
항목(10~20개)마다 붙은 `[근거: ...]`가 실제 INVARIANTS.md 행·RED_TEAM_FINDINGS.md
RTF 항목·ADR/spec 문서(#앵커)·git 커밋·문서 내 리프/이니셔티브 ID 중 하나로
해석 가능하고, `[검사: ...]`가 실제 스크립트/테스트 파일(옵션 `::함수명`) 또는
후보 표시("후보")이며, 반례 코드블록이 2개 이상, "이 축에서 났던 사고" 절이
실제로 존재하는 git 커밋을 3건 이상 인용하는지 확인한다.

git 커밋 조회는 이 저장소 안에서만 하고(subprocess, 네트워크 없음), 객체 존재뿐
아니라 `origin/main`(원격이 없으면 HEAD)의 조상인지까지 `git merge-base
--is-ancestor`로 확인한다 -- 원격 fetch는 하지 않는다. 객체가 로컬 odb에 있어도
(예: 다른 워크트리 전용 원격에서 fetch된 loose object) 검토 대상 브랜치의 조상이
아니면 근거로 인정하지 않는다 -- 그런 커밋은 origin만 clone하는 CI에서는 애초에
객체조차 존재하지 않아 거짓 녹색(로컬에서만 통과)이 된다(task-7435).
"""

from __future__ import annotations

import re
import subprocess
from functools import cache
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = ROOT / ".claude" / "skills"
DOCS_DIR = ROOT / "docs"

REQUIRED_AXES = {
    "safety",
    "ledger",
    "migration",
    "exchange",
    "dsl",
    "frontend",
    "data",
    "ops",
}
VALID_TIERS = {"S", "M", "L"}

_GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_ITEM_START_RE = re.compile(r"^\d+\.\s+", re.MULTILINE)
_BULLET_START_RE = re.compile(r"^-\s+", re.MULTILINE)
_REF_CHECK_RE = re.compile(r"\[근거:\s*([^\]]+)\]\s*\[검사:\s*([^\]]+)\]")
_INCIDENT_SHA_RE = re.compile(r"\(git:([0-9a-f]{7,40})\)")


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("review-*/SKILL.md"))


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    assert text.startswith("---\n"), "SKILL.md는 YAML frontmatter로 시작해야 한다"
    end = text.index("\n---", 4)
    frontmatter = yaml.safe_load(text[4:end])
    body = text[end + 4 :]
    return frontmatter, body


def _section(body: str, heading: str) -> str:
    marker = f"## {heading}"
    assert marker in body, f"'{marker}' 절이 없다"
    rest = body.split(marker, 1)[1]
    return rest.split("\n## ", 1)[0]


@cache
def _reachability_target() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "origin/main"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return "origin/main" if result.returncode == 0 else "HEAD"


def _is_ancestor(sha: str, target: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, target],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


@cache
def _git_commit_exists(sha: str) -> bool:
    return _is_ancestor(sha, _reachability_target())


@cache
def _doc_contains(token: str) -> bool:
    needle = token.encode("utf-8")
    for path in DOCS_DIR.rglob("*.md"):
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if needle in content:
            return True
    return False


def _strip_paren(token: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", token).strip()


_DOC_FILENAME_RE = re.compile(r"^([\w.\-]+\.(?:md|toml|py|mjs))\b(.*)$")


@cache
def _find_doc(filename: str) -> Path | None:
    matches = list(DOCS_DIR.rglob(filename))
    if matches:
        return matches[0]
    candidate = ROOT / filename
    return candidate if candidate.exists() else None


def _resolve_ref(raw_ref: str) -> bool:
    ref = raw_ref.strip()
    if re.fullmatch(r"I-\d{2}", ref):
        invariants = (DOCS_DIR / "design" / "INVARIANTS.md").read_text(encoding="utf-8")
        return f"| {ref} |" in invariants
    if re.fullmatch(r"RTF-\d+", ref):
        red_team = (DOCS_DIR / "RED_TEAM_FINDINGS.md").read_text(encoding="utf-8")
        return ref in red_team
    if ref.startswith("ADR-"):
        return any((DOCS_DIR / "design").glob(f"{ref}*.md"))
    if ref.startswith("spec:"):
        remainder = ref[len("spec:") :]
        filename, _, anchor = remainder.partition("#")
        doc = _find_doc(filename)
        if doc is None:
            return False
        if not anchor:
            return True
        return anchor in doc.read_text(encoding="utf-8")
    if ref.startswith("git:"):
        return _git_commit_exists(ref[len("git:") :])
    if ref.startswith("scripts/") or ref.startswith("tests/"):
        path_part, _, func = ref.partition("::")
        target = ROOT / _strip_paren(path_part)
        if not target.exists():
            return False
        if not func:
            return True
        return _strip_paren(func) in target.read_text(encoding="utf-8")
    if _GIT_SHA_RE.fullmatch(ref):
        return _git_commit_exists(ref)
    doc_match = _DOC_FILENAME_RE.match(ref)
    if doc_match:
        filename, rest = doc_match.groups()
        doc = _find_doc(filename)
        if doc is None:
            return False
        rest = rest.strip()
        if not rest:
            return True
        return rest in doc.read_text(encoding="utf-8")
    return _doc_contains(ref)


def _resolve_check(raw_check: str) -> bool:
    check = raw_check.strip()
    if check == "후보":
        return True
    path_part, _, func = check.partition("::")
    target = ROOT / _strip_paren(path_part)
    if not target.exists():
        return False
    if not func:
        return True
    return _strip_paren(func) in target.read_text(encoding="utf-8")


def _split_blocks(section: str, start_re: re.Pattern[str]) -> list[str]:
    starts = [m.start() for m in start_re.finditer(section)]
    starts.append(len(section))
    return [section[starts[i] : starts[i + 1]] for i in range(len(starts) - 1)]


def _checklist_items(body: str) -> list[str]:
    return _split_blocks(_section(body, "체크리스트"), _ITEM_START_RE)


def _incident_bullets(body: str) -> list[str]:
    return _split_blocks(_section(body, "이 축에서 났던 사고"), _BULLET_START_RE)


def test_ancestor_guard_rejects_reachable_but_non_ancestor_commit() -> None:
    """워크트리 전용 원격에서 fetch된 커밋처럼, 조상 관계가 아닌 커밋은 거부한다."""
    target = _reachability_target()
    old_sha = subprocess.run(
        ["git", "rev-list", "-1", "--skip=200", target],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    head_sha = subprocess.run(
        ["git", "rev-parse", target],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert old_sha and head_sha and old_sha != head_sha
    assert _is_ancestor(old_sha, head_sha), "old_sha는 head_sha의 조상이어야 한다"
    assert not _is_ancestor(head_sha, old_sha), (
        "head_sha는 old_sha의 조상이 아니므로 거부돼야 한다 -- "
        "이 실패는 가드가 도달성을 확인하지 않고 있다는 뜻이다"
    )


def test_exactly_eight_skill_files() -> None:
    files = _skill_files()
    assert len(files) == 8, f"review-* SKILL.md 8개가 필요하다: {files}"


def test_all_required_axes_present() -> None:
    axes = set()
    for path in _skill_files():
        frontmatter, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        axes.add(frontmatter["axis"])
    assert axes == REQUIRED_AXES


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_frontmatter_loadable_by_review_plan(skill_path: Path) -> None:
    frontmatter, _ = _split_frontmatter(skill_path.read_text(encoding="utf-8"))
    paths = frontmatter.get("paths")
    assert isinstance(paths, list) and paths, "paths는 비어있지 않은 리스트여야 한다"
    assert all(isinstance(p, str) for p in paths)
    tier = frontmatter.get("tier")
    assert isinstance(tier, list) and tier, "tier는 비어있지 않은 리스트여야 한다"
    assert set(tier) <= VALID_TIERS, f"알 수 없는 tier: {tier}"
    axis = frontmatter.get("axis")
    assert axis in REQUIRED_AXES, f"알 수 없는 axis: {axis}"
    assert skill_path.parent.name == f"review-{axis}", "디렉터리명과 axis가 일치해야 한다"


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_checklist_item_count_in_range(skill_path: Path) -> None:
    _, body = _split_frontmatter(skill_path.read_text(encoding="utf-8"))
    items = _checklist_items(body)
    assert 10 <= len(items) <= 20, (
        f"{skill_path.parent.name}: 체크리스트 {len(items)}항 (10~20 필요)"
    )


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_checklist_items_have_resolvable_citations(skill_path: Path) -> None:
    _, body = _split_frontmatter(skill_path.read_text(encoding="utf-8"))
    items = _checklist_items(body)
    for item in items:
        match = _REF_CHECK_RE.search(item)
        assert match, f"{skill_path.parent.name}: 항목에 [근거:]/[검사:] 태그가 없다:\n{item[:80]}"
        refs, check = match.groups()
        ref_list = [r.strip() for r in refs.split(",")]
        assert ref_list and all(ref_list), f"{skill_path.parent.name}: 빈 근거 토큰"
        for ref in ref_list:
            assert _resolve_ref(ref), f"{skill_path.parent.name}: 근거 '{ref}' 해석 불가"
        assert _resolve_check(check), f"{skill_path.parent.name}: 검사 '{check}' 해석 불가"


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_has_at_least_two_counterexamples(skill_path: Path) -> None:
    _, body = _split_frontmatter(skill_path.read_text(encoding="utf-8"))
    section = _section(body, "반례")
    fence_count = section.count("```")
    assert fence_count >= 4, f"{skill_path.parent.name}: 반례 코드블록 2개 미만"


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_has_at_least_three_real_incidents(skill_path: Path) -> None:
    _, body = _split_frontmatter(skill_path.read_text(encoding="utf-8"))
    bullets = _incident_bullets(body)
    resolved: list[str] = []
    for bullet in bullets:
        match = _INCIDENT_SHA_RE.search(bullet)
        assert match, (
            f"{skill_path.parent.name}: 사고 항목에 (git:<sha>) 인용이 없다:\n{bullet[:80]}"
        )
        sha = match.group(1)
        assert _git_commit_exists(sha), f"{skill_path.parent.name}: git 커밋 {sha} 미해석"
        resolved.append(sha)
    assert len(resolved) >= 3, (
        f"{skill_path.parent.name}: 사고 인용 {len(resolved)}건 (3건 이상 필요)"
    )
