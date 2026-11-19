"""BYOAI 호환성 등록부 검사·표 생성(ADR-2026-11-20-B D9).

벤더 플랜·UI·스펙은 자주 바뀐다. 그래서 호환성은 사람이 쓰는 표가 아니라
데이터(`docs/ops/byoai_compat.json`)로 두고, 이 스크립트가 다음을 한다.
  (1) 표(`docs/ops/BYOAI_COMPAT.md`)를 데이터에서 생성한다.
  (2) 형식을 검사한다(근거 없는 Verified 금지).
  (3) 확인 기한을 넘긴 항목을 "재확인 필요"로 **내린다**.
자동 검사는 등급을 내리기만 한다. 올리는 것은 실제 클라이언트로 확인한
증거가 있을 때 사람이 한다.

    python scripts/byoai_compat.py --render        # 표 생성
    python scripts/byoai_compat.py --check         # 형식 검사 + 표가 최신인지 (CI 게이트)
    python scripts/byoai_compat.py --overdue       # 확인 기한이 지난 항목 목록(알림용, 종료코드 0)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "ops" / "byoai_compat.json"
TABLE = ROOT / "docs" / "ops" / "BYOAI_COMPAT.md"
STATUSES = ("verified", "compatible", "manual", "unverified")
HEALTH_STATES = ("ok", "degraded", "down", "unknown")
HEALTH_LABEL = {"ok": "정상", "degraded": "저하", "down": "중단", "unknown": "-"}
LABEL = {
    "verified": "Verified",
    "compatible": "Compatible",
    "manual": "Manual",
    "unverified": "미확인",
}
NL = chr(10)


def load(path: Path = DATA) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def validate(data: dict[str, Any]) -> list[str]:
    """형식 오류 목록. verified는 확인일과 근거가 있어야 한다(근거 없는 Verified 금지)."""
    errors: list[str] = []
    seen: set[str] = set()
    for a in data.get("agents") or []:
        aid = str(a.get("id") or "")
        if not aid or aid in seen:
            errors.append(f"id 없음 또는 중복: {aid!r}")
        seen.add(aid)
        if a.get("status") not in STATUSES:
            errors.append(f"{aid}: 알 수 없는 등급 {a.get('status')!r}")
        health = a.get("health") or {}
        if health and health.get("state") not in HEALTH_STATES:
            errors.append(f"{aid}: 알 수 없는 health 상태 {health.get('state')!r}")
        if health.get("state") in ("degraded", "down") and not health.get("incident"):
            errors.append(f"{aid}: degraded/down은 incident(사건 id)가 필요하다")
        if a.get("status") == "verified" and not (a.get("verified_at") and a.get("evidence")):
            errors.append(f"{aid}: verified는 verified_at과 evidence가 필요하다")
    return errors


def due_date(agent: dict[str, Any], default_days: int) -> dt.date | None:
    if not agent.get("verified_at"):
        return None
    days = int(agent.get("review_every_days") or default_days)
    return dt.date.fromisoformat(agent["verified_at"]) + dt.timedelta(days=days)


def effective_status(agent: dict[str, Any], default_days: int, today: dt.date) -> str:
    """기한을 넘긴 verified/compatible은 '재확인 필요'로 내려 보인다(등급을 올리는 일은 없다)."""
    due = due_date(agent, default_days)
    if agent.get("status") in ("verified", "compatible") and due and due < today:
        return "stale"
    return str(agent.get("status"))


def overdue(data: dict[str, Any], today: dt.date) -> list[dict[str, Any]]:
    days = int(data.get("review_every_days") or 90)
    return [a for a in data.get("agents") or [] if effective_status(a, days, today) == "stale"]


def _cells(a: dict[str, Any], days: int, today: dt.date) -> list[str]:
    status = effective_status(a, days, today)
    due = due_date(a, days)
    label = "재확인 필요" if status == "stale" else LABEL.get(status, status)
    src = ", ".join(f"[링크]({u})" for u in a.get("sources") or []) or "-"
    return [
        str(a.get("name")),
        str(a.get("channel")),
        label,
        str(a.get("verified_at") or "-"),
        str(due) if due else "-",
        HEALTH_LABEL.get(str((a.get("health") or {}).get("state") or "unknown"), "-"),
        src,
    ]


def render(data: dict[str, Any], today: dt.date | None = None) -> str:
    today = today or dt.date(1970, 1, 1)  # 파일에는 날짜에 따라 변하는 '재확인 필요'를 쓰지 않는다
    days = int(data.get("review_every_days") or 90)
    head = [
        "# BYOAI 에이전트 호환성 (자동 생성 -- 수정하지 말 것)",
        "",
        "원천: `docs/ops/byoai_compat.json`.",
        "생성: `python scripts/byoai_compat.py --render`. 규칙: ADR-2026-11-20-B D7·D9.",
        "등급은 자동 검사가 **내리기만** 한다.",
        "Verified는 실제 클라이언트로 확인한 증거(날짜·근거)가 있을 때만 사람이 올린다.",
        "운영 상태(health)는 관측이라 자동으로 오르내린다(ADR-2026-11-20-B D10).",
        f"확인 주기: {days}일. 기한이 지나면 대시보드·소견이 '재확인 필요'로 표시한다.",
        "",
        "| 에이전트 | 채널 | 등급 | 마지막 확인 | 다음 확인 | 운영 상태 | 출처 |",
        "|---|---|---|---|---|---|---|",
    ]
    rows = ["| " + " | ".join(_cells(a, days, today)) + " |" for a in data.get("agents") or []]
    return NL.join(head + rows) + NL


def check(today: dt.date) -> int:
    data = load()
    errors = validate(data)
    expected = render(data)
    current = TABLE.read_text(encoding="utf-8") if TABLE.exists() else ""
    if current.replace(chr(13) + NL, NL) != expected:
        errors.append(
            "docs/ops/BYOAI_COMPAT.md가 최신이 아니다: python scripts/byoai_compat.py --render"
        )
    for e in errors:
        print(f"ERROR: {e}")
    late = overdue(data, today)
    for a in late:
        print(f"재확인 필요: {a['id']} (마지막 확인 {a.get('verified_at')})")
    if not errors:
        print(
            f"OK: byoai 호환성 등록부 {len(data.get('agents') or [])}건, 재확인 필요 {len(late)}건"
        )
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--overdue", action="store_true")
    args = parser.parse_args(argv)
    today = dt.date.today()
    if args.render:
        TABLE.write_text(render(load()), encoding="utf-8", newline=NL)
        print(f"wrote {TABLE.relative_to(ROOT).as_posix()}")
    if args.overdue:
        for a in overdue(load(), today):
            print(a["id"])
    if args.check:
        return check(today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
