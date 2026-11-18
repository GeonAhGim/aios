"""Plan one small leaf per open spec block (the spec issuer brain).

Each leaf fills exactly one block of one screen spec. The leaf text embeds the few facts the block
needs (taken from the code by `extract_facts`), the exact rules, and a machine-checkable definition
of done, so a small local model never has to explore or invent.

    python -m scripts.frontend_spec.plan_leaves [--screen ID] [--block KEY] [--json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from scripts.frontend_spec import extract_facts as ef
from scripts.frontend_spec import gen_facts, skeleton

MAX_SPEC_CHARS = 1450
INSTRUCTIONS_FILE = Path(__file__).with_name("instructions.json")
NL = chr(10)
LOCAL_BLOCKS = ("p1", "data", "st-a", "st-b", "st-c", "act", "err", "quality", "money")
CLAUDE_BLOCKS = ("accept", "gap")


@dataclass(frozen=True)
class Plan:
    screen: str
    block: str
    title: str
    doc: str
    spec: str
    tier: str


def _facts_lines(page: ef.PageFacts, kind: str, limit: int) -> list[str]:
    return [
        f"- {f.name} (:{f.line})" + (f" [{f.extra}]" if f.extra else "")
        for f in ef._unique(page.of(kind))
    ][:limit]


def _text_lines(page: ef.PageFacts, catalogs: dict[str, dict[str, str]], limit: int) -> list[str]:
    ko = catalogs.get("ko", {})
    return [
        f"- `{f.name}` {ko.get(f.name, '(카탈로그 없음)')} (:{f.line})"
        for f in ef._unique(page.of("text"))
    ][:limit]


def excerpt(page: ef.PageFacts, catalogs: dict[str, dict[str, str]], key: str) -> str:
    """Only the facts a given block needs, ordered by importance (the tail is what gets cut)."""
    errors = ["오류 처리 호출:", *_facts_lines(page, "error", 8)]
    texts = ["화면 문구:", *_text_lines(page, catalogs, 14)]
    if key in ("st-b", "st-c"):
        return NL.join(errors + texts)
    if key in ("st-a", "p1", "money"):
        return NL.join(texts)
    if key == "act":
        return NL.join(["버튼·핸들러:", *_facts_lines(page, "button", 10)])
    if key == "err":
        return NL.join(errors)
    if key == "quality":
        ko = catalogs.get("ko", {})
        missing = [f.name for f in ef._unique(page.of("text")) if f.name not in ko][:8]
        a11y = _facts_lines(page, "a11y", 6) + _facts_lines(page, "testid", 6)
        return NL.join(
            ["a11y/testid:", *a11y, "카탈로그 누락 키: " + (", ".join(missing) or "없음")]
        )
    return ""


def load_instructions() -> tuple[str, dict[str, str]]:
    data = json.loads(INSTRUCTIONS_FILE.read_text(encoding="utf-8"))
    return str(data["rules"]), {str(k): str(v) for k, v in data["blocks"].items()}


def fit(fixed: str, facts: str, limit: int = MAX_SPEC_CHARS) -> str:
    """Fixed instructions are never cut; only the fact excerpt shrinks, line by line."""
    room = limit - len(fixed) - 1
    kept: list[str] = []
    used = 0
    for line in facts.splitlines():
        if used + len(line) + 1 > room:
            break
        kept.append(line)
        used += len(line) + 1
    return fixed + (NL + NL.join(kept) if kept else "")


def build_plan(item: dict[str, str], key: str, catalogs: dict[str, dict[str, str]]) -> Plan:
    sid = skeleton.screen_id(item)
    doc = f"docs/frontend/screens/{sid}.md"
    stem = Path(item["page"]).stem
    page = ef.extract(ef.ROOT / item["page"])
    rules, blocks = load_instructions()
    target = f"{doc} (facts: docs/frontend/facts/{stem}.facts.md, source {item['page']})"
    block_text = blocks.get(key, "Fill the block.").format(route=item["route"])
    fixed = NL.join(
        [f"대상: {target}.", block_text, rules.format(sid=sid, key=key, doc=doc), "사실:"]
    )
    spec = fit(fixed, excerpt(page, catalogs, key))
    head = f"[FE-SPEC] {sid} / {skeleton.BLOCK_BY_KEY[key].title}"
    tier = "claude" if key in CLAUDE_BLOCKS else "local"
    return Plan(sid, key, head, doc, spec, tier)


def open_blocks(screen: str) -> list[str]:
    path = skeleton.SCREENS_DIR / f"{screen}.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [b.key for b in skeleton.BLOCKS if (skeleton.todo_count(text, screen, b.key) or 0) > 0]


def plan_all(screen: str | None = None, block: str | None = None) -> list[Plan]:
    catalogs = ef.load_catalogs()
    plans: list[Plan] = []
    for item in gen_facts.screens():
        sid = skeleton.screen_id(item)
        if screen and sid != screen:
            continue
        for key in open_blocks(sid):
            if block and key != block:
                continue
            plans.append(build_plan(item, key, catalogs))
    return plans


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen")
    parser.add_argument("--block")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    plans = plan_all(args.screen, args.block)
    if args.json:
        print(json.dumps([p.__dict__ for p in plans], ensure_ascii=False, indent=1))
    else:
        for plan in plans:
            print(f"{plan.tier:6} {plan.screen} {plan.block} len={len(plan.spec)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
