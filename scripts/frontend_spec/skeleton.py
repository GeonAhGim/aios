"""Screen-spec skeletons: machine-filled structure plus small, individually fillable blocks.

The skeleton gives a local worker nothing to invent: the facts tables are already generated, and
each judgement cell is a `TODO` inside a named block. One leaf fills one block. Completion is
machine-checkable: no `TODO` left in the block and `verify_spec` reports nothing.

    python -m scripts.frontend_spec.skeleton --write     # create missing skeletons (no overwrite)
    python -m scripts.frontend_spec.skeleton --status         # TODO count per screen and block
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from scripts.frontend_spec import extract_facts as ef
from scripts.frontend_spec import gen_facts

ROOT = ef.ROOT
SCREENS_DIR = ROOT / "docs" / "frontend" / "screens"
TODO = "TODO"

STATES_A = ("로딩", "빈 상태", "정상", "낡은 데이터(신선도 초과)")
STATES_B = (
    "입력 오류(400/검증)",
    "인증 필요/세션 만료(401)",
    "권한 없음/MFA 필요(403)",
    "충돌/상태 불일치(409)",
)
STATES_C = (
    "잠김/속도 제한(423/429)",
    "서버 오류(5xx)",
    "네트워크 단절",
    "부분 실패(일부 데이터만 실패)",
)


@dataclass(frozen=True)
class Block:
    key: str
    title: str
    header: str
    rows: tuple[str, ...]


def _rows(names: tuple[str, ...], cells: int) -> tuple[str, ...]:
    return tuple("| " + " | ".join([name] + [TODO] * cells) + " |" for name in names)


BLOCKS: tuple[Block, ...] = (
    Block(
        "p1",
        "1. 개요 보충 (페르소나·목적·진입 조건·준비물)",
        "| 항목 | 내용 |\n|---|---|",
        _rows(("주 페르소나(P1~P11)", "목적(한 문장)", "진입 조건", "사용자 준비물"), 1),
    ),
    Block(
        "data",
        "2. 데이터와 계약",
        "| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |\n|---|---|---|---|",
        (),
    ),
    Block(
        "st-a",
        "3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터",
        "| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |\n|---|---|---|---|---|",
        _rows(STATES_A, 4),
    ),
    Block(
        "st-b",
        "3-b. 상태 매트릭스: 입력 오류·401·403·409",
        "| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |\n|---|---|---|---|---|",
        _rows(STATES_B, 4),
    ),
    Block(
        "st-c",
        "3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패",
        "| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |\n|---|---|---|---|---|",
        _rows(STATES_C, 4),
    ),
    Block(
        "act",
        "4. 사용자 행동과 전이",
        "| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |\n|---|---|---|---|---|",
        (),
    ),
    Block(
        "money",
        "5. 금전·위험 행동 규칙",
        "| 항목 | 내용 |\n|---|---|",
        _rows(("해당 여부와 이유(없으면 '해당 없음' + 이유)",), 1),
    ),
    Block(
        "err",
        "6. 오류 문구와 복구 안내",
        "| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |\n|---|---|---|---|",
        (),
    ),
    Block(
        "quality",
        "7·8·9·10. 접근성·i18n·성능·관측",
        "| 영역 | 확인한 사실 | 갭 |\n|---|---|---|",
        _rows(("접근성(WCAG 2.2 AA)", "i18n/지역화", "성능 예산", "텔레메트리"), 2),
    ),
    Block(
        "accept",
        "11. 수용 기준",
        "| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |\n|---|---|---|",
        _rows(("A1", "A2", "A3"), 2),
    ),
    Block(
        "gap",
        "12. 구현 대비 갭 표",
        "| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |\n|---|---|---|---|---|",
        _rows(("G1", "G2", "G3"), 4),
    ),
)
BLOCK_BY_KEY = {b.key: b for b in BLOCKS}
MARK_RE = re.compile(
    r"<!-- BLOCK:(?P<id>[^:]+):(?P<key>[a-z0-9-]+) -->\n(?P<body>.*?)<!-- /BLOCK -->", re.S
)


def screen_id(item: dict[str, str]) -> str:
    return f"{item['journey']}-{Path(item['page']).stem}"


def _auto_rows(page: ef.PageFacts, key: str, defs: dict[str, tuple[str, int]]) -> tuple[str, ...]:
    """Rows whose first cells are machine facts; the remaining cells stay TODO."""

    def ref(line: int) -> str:
        return f"`{page.path}:{line}`"

    if key == "data":
        rows = []
        for fact in ef._unique(page.of("hook")):
            where = defs.get(fact.name)
            if where:
                rows.append(f"| `{fact.name}` | `{where[0]}:{where[1]}` | {TODO} | {TODO} |")
        return tuple(rows)
    if key == "act":
        return tuple(
            f"| `<{f.name}>` ({ref(f.line)}) | {TODO} | {TODO} | {TODO} | {TODO} |"
            for f in page.of("button")
        )
    if key == "err":
        return tuple(
            f"| `{f.name}` ({ref(f.line)}) | {TODO} | {TODO} | {TODO} |"
            for f in ef._unique(page.of("error"))
        )
    return ()


def render_block(screen: str, block: Block, extra: tuple[str, ...]) -> str:
    rows = block.rows + extra
    body = "\n".join(rows) if rows else f"| {TODO} |" + " |" * (block.header.count("|") // 2 - 3)
    return (
        f"### {block.title}\n\n<!-- BLOCK:{screen}:{block.key} -->\n"
        f"{block.header}\n{body}\n<!-- /BLOCK -->\n"
    )


def build(item: dict[str, str], defs: dict[str, tuple[str, int]]) -> str:
    page_path = ROOT / item["page"]
    page = ef.extract(page_path)
    sid = screen_id(item)
    head = (
        f"# {sid} — `{item['route']}`\n\n"
        f"> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/{page_path.stem}.facts.md`. "
        "`TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.\n\n"
        "## 1. 개요\n\n"
        f"- 화면 / 라우트 / 소스: `{item['route']}` / `{page.path}`\n"
        f"- 여정 소속: {item['journey']}\n\n"
    )
    parts = [head]
    for block in BLOCKS:
        parts.append(render_block(sid, block, _auto_rows(page, block.key, defs)))
    parts.append(
        "### 13. 미규정 액션 후보\n\n- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)\n"
    )
    return "\n".join(parts)


def todo_count(text: str, screen: str, key: str) -> int | None:
    """TODO markers inside one block; None if the block is missing."""
    for match in MARK_RE.finditer(text):
        if match.group("id") == screen and match.group("key") == key:
            return match.group("body").count(TODO)
    return None


def status() -> dict[str, dict[str, int | None]]:
    out: dict[str, dict[str, int | None]] = {}
    for item in gen_facts.screens():
        sid = screen_id(item)
        path = SCREENS_DIR / f"{sid}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        out[sid] = {b.key: todo_count(text, sid, b.key) for b in BLOCKS}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    if args.write:
        defs = ef.hook_definitions()
        SCREENS_DIR.mkdir(parents=True, exist_ok=True)
        made = 0
        for item in gen_facts.screens():
            path = SCREENS_DIR / f"{screen_id(item)}.md"
            if not path.exists():
                path.write_text(build(item, defs), encoding="utf-8")
                made += 1
        print(f"created {made} skeleton(s)")
    if args.status:
        for sid, blocks in status().items():
            open_blocks = {k: v for k, v in blocks.items() if v}
            print(f"{sid}: open blocks {open_blocks if open_blocks else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
