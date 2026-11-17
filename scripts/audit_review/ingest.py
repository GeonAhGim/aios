"""Codex 감사 체크포인트(.md)의 지적을 findings.jsonl에 증분으로 합친다 -- 2026-11-18.

사용: `python -m scripts.audit_review.ingest --src <체크포인트 폴더> [--out 경로]`
(기본 출력은 docs/audit_review_codex_2026-11-14/findings.jsonl).
키는 (체크포인트, 지적 ID)이고 이미 있는 기록의 검토 필드(상태·판정·증거 등)는
절대 덮어쓰지 않는다. 원문이 바뀌었으면 `source_changed`만 표시한다.
읽기 전용이다(원본 폴더는 건드리지 않는다).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from scripts.audit_review.schema import Record, key_of, new_record

DEFAULT_OUT = Path("docs/audit_review_codex_2026-11-14/findings.jsonl")
CHECKPOINT_GLOB = "AIOS_ADVERSARIAL_AUDIT_CHECKPOINT_*.md"
HEAD_RE = re.compile(r"^## (?P<id>[A-Z]{1,4}-\d+[a-z]?)\s*(?P<rest>.*)$")
SEVERITY_RE = re.compile(r"심각도\s*[:：]\s*(치명|높음|중간|보통|낮음)")


def parse_checkpoint(text: str, cp: str) -> list[Record]:
    """한 체크포인트의 `## X-N ...` 섹션들을 기록으로 바꾼다."""
    blocks: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    for line in text.splitlines():
        m = HEAD_RE.match(line)
        if m:
            current = (m["id"], m["rest"].strip(" —-:"), [])
            blocks.append(current)
        elif line.startswith("## "):
            current = None  # 지적이 아닌 섹션(정상 보호·재개 지점 등)
        elif current is not None:
            current[2].append(line)
    out = []
    for fid, title, body_lines in blocks:
        body = "\n".join(body_lines).strip()
        sev = SEVERITY_RE.search(body)
        out.append(new_record(cp, fid, title, body, sev.group(1) if sev else None))
    return out


def load(path: Path) -> list[Record]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def merge(existing: list[Record], incoming: list[Record]) -> tuple[list[Record], dict[str, int]]:
    """기존 기록을 보존하며 새 지적을 더한다. (결과, 집계)."""
    by_key = {key_of(r): r for r in existing}
    stats = {"new": 0, "kept": 0, "source_changed": 0}
    for rec in incoming:
        old = by_key.get(key_of(rec))
        if old is None:
            by_key[key_of(rec)] = rec
            stats["new"] += 1
            continue
        stats["kept"] += 1
        if old.get("body") != rec["body"] or old.get("title") != rec["title"]:
            if old.get("state") == "DISCOVERED":
                old.update(
                    title=rec["title"], body=rec["body"], severity_label=rec["severity_label"]
                )
            else:
                old["source_changed"] = True  # 검토된 기록의 원문이 바뀜 -- 사람이 다시 본다
                stats["source_changed"] += 1
    return sorted(by_key.values(), key=_sort_key), stats


def _sort_key(rec: Record) -> tuple[str, str, int, str]:
    m = re.match(r"([A-Z]+)-(\d+)", str(rec["id"]))
    return (str(rec["cp"]), m.group(1) if m else "", int(m.group(2)) if m else 0, str(rec["id"]))


def write(path: Path, records: list[Record]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records),
        encoding="utf-8",
    )
    tmp.replace(path)


def ingest(src: Path, out: Path) -> dict[str, int]:
    incoming: list[Record] = []
    for md in sorted(src.glob(CHECKPOINT_GLOB)):
        cp = md.stem.rsplit("_", 1)[-1]
        incoming.extend(parse_checkpoint(md.read_text(encoding="utf-8", errors="replace"), cp))
    merged, stats = merge(load(out), incoming)
    write(out, merged)
    stats["total"] = len(merged)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True, help="Codex 체크포인트 .md가 있는 폴더")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    if not args.src.is_dir():
        print(f"원본 폴더가 없다: {args.src}", file=sys.stderr)
        return 2
    print(json.dumps(ingest(args.src, args.out), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
