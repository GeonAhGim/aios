"""감사 지적 기록의 현황 집계·규칙 점검·상태 갱신 -- 2026-11-18.

사용:
  status                  상태·우선순위별 집계
  status --check          규칙 위반이 있으면 종료코드 1(증거 인용 실재 포함)
  status set <cp> <id> --state VERIFIED --verdict still_valid
      --evidence "경로|줄|코드 원문 인용" [--category order --severity high
      --priority A --fix-task 123 --note ...]
(모두 `python -m scripts.audit_review.status ...`로 실행한다.)
`set`은 상태 전이·증거 인용·필수 필드를 검사하고 통과해야만 파일에 쓴다.
손으로 JSONL을 고치지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from scripts.audit_review.ingest import DEFAULT_OUT, load, write
from scripts.audit_review.schema import ALL_STATES, Record, record_problems, transition_problems


def repo_reader(root: Path) -> Callable[[str], str | None]:
    def read(rel: str) -> str | None:
        path = root / rel
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None

    return read


def summary(records: list[Record]) -> str:
    by_state = Counter(r["state"] for r in records)
    lines = [f"지적 {len(records)}건"]
    lines += [f"  {s:<10} {by_state.get(s, 0)}" for s in ALL_STATES if by_state.get(s)]
    pri = Counter(r.get("priority") or "-" for r in records if r["state"] not in ("DISCOVERED",))
    if pri:
        lines.append(
            "  우선순위(분류 이후): " + ", ".join(f"{k}:{v}" for k, v in sorted(pri.items()))
        )
    return "\n".join(lines)


def check_all(records: list[Record], root: Path) -> list[str]:
    read = repo_reader(root)
    return [f"{r['cp']} {r['id']}: {p}" for r in records for p in record_problems(r, read)]


def parse_evidence(raw: list[str]) -> list[Record]:
    out = []
    for item in raw:
        parts = item.split("|", 2)
        if len(parts) != 3:
            raise ValueError(f"--evidence는 '경로|줄|인용' 형식이다: {item!r}")
        line = int(parts[1]) if parts[1].strip().isdigit() else None
        out.append({"file": parts[0].strip(), "line": line, "quote": parts[2].strip()})
    return out


def apply_update(rec: Record, args: argparse.Namespace, root: Path) -> list[str]:
    """갱신을 적용하고 문제 목록을 돌려준다(비어 있지 않으면 rec는 바뀌지 않은 것으로 취급한다)."""
    new = dict(rec)
    for field in ("category", "severity", "priority", "verdict", "note"):
        value = getattr(args, field)
        if value is not None:
            new[field] = value
    if args.fix_task is not None:
        new["fix_task"] = args.fix_task
    if args.evidence:
        new["evidence"] = [*rec.get("evidence", []), *parse_evidence(args.evidence)]
    if args.state:
        problems = transition_problems(rec["state"], args.state)
        if problems:
            return problems
        new["state"] = args.state
    problems = record_problems(new, repo_reader(root))
    if not problems:
        rec.clear()
        rec.update(new)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--findings", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    s = sub.add_parser("set")
    s.add_argument("cp")
    s.add_argument("id")
    s.add_argument("--state")
    s.add_argument("--verdict")
    s.add_argument("--category")
    s.add_argument("--severity")
    s.add_argument("--priority")
    s.add_argument("--fix-task", type=int, dest="fix_task")
    s.add_argument("--note")
    s.add_argument("--evidence", action="append", default=[])
    args = parser.parse_args(argv)
    records = load(args.findings)
    if args.cmd == "set":
        rec = next((r for r in records if (r["cp"], r["id"]) == (args.cp, args.id)), None)
        if rec is None:
            print(f"기록에 없다: {args.cp} {args.id}", file=sys.stderr)
            return 2
        problems = apply_update(rec, args, args.repo_root)
        if problems:
            print("거부:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        write(args.findings, records)
        print(json.dumps({k: rec[k] for k in ("cp", "id", "state", "verdict")}, ensure_ascii=False))
        return 0
    print(summary(records))
    if args.check:
        problems = check_all(records, args.repo_root)
        if problems:
            print("규칙 위반:\n  " + "\n  ".join(problems[:50]), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
