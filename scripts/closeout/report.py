"""closeout 결과 마크다운 렌더링 -- task-6475 분할 조각.

`scripts/closeout_check.py`의 책임 분할: 이 모듈은 `CheckResult` 목록을
사람이 읽는 표/증빙 마크다운으로 바꾸고, 전항 PASS일 때만 종료 확인서
파일로 쓰는 순수 렌더링만 담당한다(판정 로직 없음).
"""

from __future__ import annotations

from pathlib import Path

from scripts.closeout.common import CheckResult


def render_markdown(results: list[CheckResult]) -> str:
    lines = ["| # | 항목 | 판정 | 요약 |", "|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"| {i} | {r.title} | {mark} | {r.detail} |")
    lines.append("")
    lines.append("## 증빙")
    for i, r in enumerate(results, 1):
        lines.append(f"\n### {i}. {r.title} — {'PASS' if r.passed else 'FAIL'}")
        for e in r.evidence:
            lines.append(f"- {e}")
    return "\n".join(lines) + "\n"


def write_closeout_doc(path: Path, results: list[CheckResult]) -> None:
    body = (
        "# MVP-1 종료 확인서 (CLOSEOUT)\n\n"
        "ADR-2026-09-04-D 종료 기준 1~10 + ADR-2026-09-09-B 11항(H-1~13) — "
        "`scripts/closeout_check.py` 전항 PASS 시점 스냅샷.\n\n" + render_markdown(results)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
