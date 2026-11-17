"""지적 1건의 본문 식별자로 저장소를 검색해 검토용 증거 묶음을 만든다 -- 2026-11-18.

본문의 snake_case/CamelCase 이름을 `git grep`해, 드문 이름을 맞힌 파일일수록
높은 점수를 준다(흔한 이름은 단서가 못 된다). 정의(def/class/function) 줄
주변을 창으로 보여 준다. 판정은 하지 않는다 -- 검토자가 읽을 재료만 만든다.

사용: `python -m scripts.audit_review.evidence <cp> <id> [--findings 경로] [--repo 경로]`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from scripts.audit_review.ingest import DEFAULT_OUT, load

TOK_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{4,}")
MAX_COMMON = 60  # 이 파일 수보다 많이 나오는 이름은 단서가 아니다
WINDOW = 40


def tokens(body: str, limit: int = 18) -> list[str]:
    found = dict.fromkeys(TOK_RE.findall(body))
    return [t for t in found if "_" in t or re.search(r"[a-z][A-Z]", t)][:limit]


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return r.stdout if r.returncode == 0 else ""


def rank_files(
    toks: list[str], files_for: Callable[[str], list[str]]
) -> list[tuple[str, list[str]]]:
    """(파일, 맞힌 식별자들) -- 희소한 식별자를 맞힌 파일이 위로."""
    score: dict[str, float] = {}
    matched: dict[str, list[str]] = {}
    for tok in toks:
        files = files_for(tok)
        if not files or len(files) > MAX_COMMON:
            continue
        for path in files:
            score[path] = score.get(path, 0.0) + 1.0 / len(files)
            matched.setdefault(path, []).append(tok)
    order = sorted(score, key=lambda p: (-score[p], len(p)))
    return [(p, matched[p]) for p in order]


def window(text: str, toks: list[str], width: int = WINDOW) -> str:
    lines = text.splitlines()
    for tok in toks:
        pat = re.compile(r"(def|class|function|const)\s+" + re.escape(tok) + r"(?![A-Za-z0-9_])")
        for n, line in enumerate(lines):
            if pat.search(line):
                return _slice(lines, max(0, n - 2), width)
    for tok in toks:
        for n, line in enumerate(lines):
            if tok in line:
                return _slice(lines, max(0, n - 6), width)
    return "\n".join(lines[:width])


def _slice(lines: list[str], start: int, width: int) -> str:
    return "\n".join(f"{start + k + 1}: {x}" for k, x in enumerate(lines[start : start + width]))


def defined_in(text: str, toks: list[str]) -> list[str]:
    """이 파일이 *정의*하는 식별자(def/class/function/const) -- import만 하는 파일과 구분한다."""
    return [
        t
        for t in toks
        if re.search(r"(def|class|function|const)\s+" + re.escape(t) + r"(?![A-Za-z0-9_])", text)
    ]


def dossier(body: str, repos: list[Path], max_files: int = 3) -> str:
    """검토자가 읽을 증거 묶음(마크다운). 관련 파일을 못 찾으면 그 사실을 적는다."""
    toks = tokens(body)

    def files_for(tok: str) -> list[str]:
        out = []
        for repo in repos:
            for line in git(repo, "grep", "-l", "-w", "-F", tok, "origin/main").splitlines():
                path = line.partition(":")[2]
                if path:
                    out.append(f"{repo}|{path}")
        return out[: MAX_COMMON + 1]

    candidates = []
    for key, matched in rank_files(toks, files_for)[: max_files * 3]:
        repo, _, path = key.partition("|")
        text = git(Path(repo), "show", f"origin/main:{path}")
        if text:
            candidates.append((len(defined_in(text, matched)), path, matched, text))
    # 식별자를 *정의*하는 파일이 먼저(그 안에서는 기존 점수 순서 유지) -- import만 하는 파일은 뒤로
    candidates.sort(key=lambda c: -c[0])
    parts = [
        f"### {path} (본문 식별자: {', '.join(matched[:5])})\n{window(text, matched)}"
        for _, path, matched, text in candidates[:max_files]
    ]
    return (
        "\n\n".join(parts)
        if parts
        else "(본문 식별자와 맞는 파일을 찾지 못했다 -- 직접 찾아야 한다)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cp")
    parser.add_argument("id")
    parser.add_argument("--findings", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--repo", type=Path, action="append", help="검색할 저장소(여러 번 가능, 기본 현재 디렉터리)"
    )
    args = parser.parse_args(argv)
    rec = next((r for r in load(args.findings) if (r["cp"], r["id"]) == (args.cp, args.id)), None)
    if rec is None:
        print(f"기록에 없다: {args.cp} {args.id}", file=sys.stderr)
        return 2
    print(f"## {rec['id']} {rec['title']}\n\n{rec['body']}\n\n--- 증거 후보 ---\n")
    print(dossier(rec["body"], args.repo or [Path.cwd()]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
