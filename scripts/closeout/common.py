"""closeout_check 분할 공용 헬퍼 -- task-6475(ratchet 초과 파일 분할).

`scripts/closeout_check.py`가 800줄 관측 임계(ADR-2026-09-10-C §7)를 넘어
책임 단위(1~10번 종료 기준 / 11번 하드닝 / 12번 HEAD Actions 녹색 / 리포트
렌더링)로 분할됐다 -- `scripts/consistency/` 패키지와 같은 전례를 따른다.
이 모듈은 그 분할 조각들이 공유하는 순수 정적-분석 헬퍼만 담는다(DB·네트워크
없음).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

UNVERIFIED = "미검증(외부 리포트 미지정)"


@dataclass(frozen=True)
class CheckResult:
    key: str
    title: str
    passed: bool
    evidence: tuple[str, ...]
    detail: str


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def iter_py(repo_root: Path, *rels: str) -> list[Path]:
    out: list[Path] = []
    for rel in rels:
        base = repo_root / rel
        if base.is_file():
            out.append(base)
        elif base.is_dir():
            out.extend(p for p in sorted(base.rglob("*.py")) if "__pycache__" not in p.parts)
    return out


def has_test_def(repo_root: Path, rel: str) -> bool:
    path = repo_root / rel
    if not path.is_file():
        return False
    return re.search(r"^\s*(async )?def test_", read_text(path), re.M) is not None


def grep(repo_root: Path, rels: Sequence[str], pattern: str, flags: int = 0) -> list[str]:
    rx = re.compile(pattern, flags)
    hits: list[str] = []
    for p in iter_py(repo_root, *rels):
        for i, line in enumerate(read_text(p).splitlines(), 1):
            if rx.search(line):
                hits.append(f"{p.relative_to(repo_root).as_posix()}:{i}")
    return hits


def present_missing(repo_root: Path, *rels: str) -> tuple[list[str], list[str]]:
    present = [r for r in rels if (repo_root / r).exists()]
    missing = [r for r in rels if r not in present]
    return present, missing


def bench_passed(repo_root: Path, rel: str) -> bool | None:
    """`docs/perf/*_bench.json`의 `passed` 필드를 읽는다.

    파일이 없으면(그 자리는 `present_missing`이 이미 FAIL로 잡는다) `None`을
    돌려준다. 파일은 있는데 JSON이 깨졌거나 `passed`가 없거나 `bool`이 아니면
    "모른다=통과 아님"(ADR-D) 원칙에 따라 `False`로 fail-closed.
    """
    path = repo_root / rel
    if not path.is_file():
        return None
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return False
    passed = data.get("passed")
    return passed if isinstance(passed, bool) else False
