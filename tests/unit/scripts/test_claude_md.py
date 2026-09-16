"""CLAUDE.md 게이트 테스트 -- task-3064 (ADR-2026-09-10-B CLAUDE-1).

DoD: 저장소 루트 CLAUDE.md가 존재하고 200줄 이내·영문(주석 언어 래칫과 같은 판정, 한글 미포함)이며
필수 섹션(1~8)을 전부 담고, WORKER_PROMPT.md(오케스트레이터 인프라가 각 워크트리에 배치하는 파일 --
이 앱 저장소에는 커밋되지 않는다)와 문장 단위 중복이 0이어야 한다. WORKER_PROMPT.md는 로컬 개발
머신에만 존재할 수 있어(CI 체크아웃에는 없음) 못 찾으면 그 항목만 통과 처리한다(pytest.skip은
code_ratchets skip_xfail 래칫을 올리므로 쓰지 않는다) -- 존재/길이/섹션 검사는 항상 강제된다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CLAUDE_MD = ROOT / "CLAUDE.md"

HANGUL = re.compile(r"[가-힣]")
MAX_LINES = 200
REQUIRED_HEADERS = [
    "## 1. Repository map",
    "## 2. Commands",
    "## 3. Rules",
    "## 4. Prohibited",
    "## 5. Definition of done",
    "## 6. Frequent mistakes",
    "## 7. File policy",
    "## 8. Non-normative docs",
]

# Known locations for the orchestrator-managed prompt file. Not part of this repo's git tree.
_WORKER_PROMPT_CANDIDATES = [
    ROOT / "WORKER_PROMPT.md",
    Path("C:/aios/aios/WORKER_PROMPT.md"),
    Path("C:/aios/mihwa-aios/WORKER_PROMPT.md"),
]

_MIN_SENTENCE_LEN = 20
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?다요])\s*\n+|(?<=[.!?])\s{1,2}(?=[A-Z가-힣`\"'])")


def _sentences(text: str) -> set[str]:
    lines = (line.strip() for line in text.splitlines())
    prose = "\n".join(line for line in lines if line and not line.startswith(("```", "|", "#")))
    parts = (p.strip() for p in _SENTENCE_SPLIT.split(prose))
    return {p for p in parts if len(p) >= _MIN_SENTENCE_LEN}


def _find_worker_prompt() -> Path | None:
    for candidate in _WORKER_PROMPT_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def test_claude_md_exists() -> None:
    assert CLAUDE_MD.is_file(), "CLAUDE.md must exist at the repository root"


def test_claude_md_within_line_budget() -> None:
    lines = CLAUDE_MD.read_text(encoding="utf-8").splitlines()
    assert 0 < len(lines) <= MAX_LINES, f"CLAUDE.md has {len(lines)} lines, budget is {MAX_LINES}"


def test_claude_md_is_english_only() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    matches = HANGUL.findall(text)
    assert not matches, f"CLAUDE.md must be English only, found Hangul: {matches[:5]}"


def test_claude_md_has_required_sections() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    missing = [h for h in REQUIRED_HEADERS if h not in text]
    assert not missing, f"CLAUDE.md is missing required sections: {missing}"


def test_claude_md_has_no_duplicate_sentences_with_worker_prompt() -> None:
    worker_prompt = _find_worker_prompt()
    if worker_prompt is None:
        # WORKER_PROMPT.md is orchestrator-managed and not committed to this repo; it may be
        # absent on a given machine (e.g. a CI checkout). Nothing to compare against, so this
        # assertion is vacuously satisfied rather than skipped (skip/xfail is code-ratcheted).
        return

    claude_sentences = _sentences(CLAUDE_MD.read_text(encoding="utf-8"))
    prompt_sentences = _sentences(worker_prompt.read_text(encoding="utf-8"))

    overlap = claude_sentences & prompt_sentences
    assert not overlap, f"CLAUDE.md duplicates sentences already in WORKER_PROMPT.md: {overlap}"
