"""외부 전수감사(Codex) 지적의 기록 스키마와 상태 규칙 -- 2026-11-18.

지적 1건 = findings.jsonl 한 줄. 상태는 아래 순서로만 나아가고, 검증 이후 상태는
증거(파일:줄 + 코드 원문 인용)가 실제 저장소에 있어야 한다(`quote_problems`).
검증은 사람이나 Claude가 하고, 이 모듈은 기록의 형식과 증거의 실재만 기계로 확인한다.
로컬 LLM의 판정은 오판이 있어(2026-11-17 A-16·A-20) 라벨로 쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Record = dict[str, Any]  # findings.jsonl 한 줄

# 진행 순서. TESTED/DEPLOYED는 두지 않는다(태스크·CI가 추적, 병합·CI 통과가 CLOSED 조건).
STATES = ("DISCOVERED", "TRIAGED", "VERIFIED", "CONFIRMED", "FIXED", "CLOSED")
REJECTED = "REJECTED"  # 결함이 아님(설계 의도·이미 수정·중복) -- VERIFIED에서만 갈 수 있다
ALL_STATES = (*STATES, REJECTED)

VERDICTS = ("still_valid", "already_fixed", "by_design", "duplicate", "unclear")
CATEGORIES = ("money", "order", "auth", "data", "ops", "perf", "ui", "other")
SEVERITIES = ("critical", "high", "medium", "low")
# 출시 차단급부터 단순 개선까지. A/B/C만 먼저 처리한다(범위 폭주 방지).
PRIORITIES = ("A", "B", "C", "D", "E")

_VERIFIED_RANK = STATES.index("VERIFIED")


def new_record(cp: str, fid: str, title: str, body: str, severity_label: str | None) -> Record:
    """수집 직후의 기록. 판정·분류 필드는 비워 둔다."""
    return {
        "cp": cp,
        "id": fid,
        "title": title,
        "body": body,
        "severity_label": severity_label,
        "state": "DISCOVERED",
        "category": None,
        "severity": None,
        "priority": None,
        "verdict": None,
        "evidence": [],
        "fix_task": None,
        "note": "",
    }


def key_of(rec: Record) -> tuple[str, str]:
    return (str(rec["cp"]), str(rec["id"]))


def _rank(state: str) -> int:
    return STATES.index(state) if state in STATES else -1


def transition_problems(old: str, new: str) -> list[str]:
    """상태 전이가 허용되는가. 한 단계씩만 나아가고 되돌리지 않는다(REJECTED는 VERIFIED에서만)."""
    if old == new:
        return []
    if new == REJECTED:
        return [] if old == "VERIFIED" else [f"{old}->REJECTED는 VERIFIED에서만 가능"]
    if old not in STATES or new not in STATES:
        return [f"알 수 없는 상태 {old}->{new}"]
    if _rank(new) != _rank(old) + 1:
        return [f"{old}->{new}: 한 단계씩만 나아간다"]
    return []


def quote_problems(rec: Record, read_file: Callable[[str], str | None]) -> list[str]:
    """증거 인용이 실제 파일에 있는가. 없으면 환각이므로 기각한다."""
    out = []
    for ev in rec.get("evidence") or []:
        path, quote = str(ev.get("file") or ""), str(ev.get("quote") or "").strip()
        if not path or not quote:
            out.append(f"증거에 file/quote가 비어 있다: {ev}")
            continue
        text = read_file(path)
        if text is None:
            out.append(f"{path}: 저장소에 없는 파일")
        elif quote not in text:
            out.append(f"{path}: 인용이 파일에 없다 -- {quote[:50]!r}")
    return out


def record_problems(rec: Record, read_file: Callable[[str], str | None]) -> list[str]:
    """기록 1건의 규칙 위반. 비어 있으면 통과."""
    problems = []
    state = rec.get("state")
    if state not in ALL_STATES:
        return [f"알 수 없는 상태 {state!r}"]
    for field, allowed in (
        ("category", CATEGORIES),
        ("severity", SEVERITIES),
        ("priority", PRIORITIES),
        ("verdict", VERDICTS),
    ):
        value = rec.get(field)
        if value is not None and value not in allowed:
            problems.append(f"{field}={value!r}는 {allowed} 중 하나여야 한다")
    needs_proof = state == REJECTED or _rank(str(state)) >= _VERIFIED_RANK
    if needs_proof:
        if not rec.get("verdict"):
            problems.append(f"{state}인데 verdict가 없다")
        if not rec.get("evidence"):
            problems.append(f"{state}인데 증거(file/line/quote)가 없다")
        problems.extend(quote_problems(rec, read_file))
    if _rank(str(state)) >= STATES.index("CONFIRMED") and not (
        rec.get("severity") and rec.get("priority")
    ):
        problems.append(f"{state}인데 severity/priority가 없다")
    if _rank(str(state)) >= STATES.index("FIXED") and not rec.get("fix_task"):
        problems.append(f"{state}인데 fix_task가 없다")
    return problems
