"""scripts/byoai_compat.py: 자동 검사는 등급을 내리기만 하고 근거 없는 Verified는 막는다."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "byoai_compat",
    Path(__file__).resolve().parents[3] / "scripts" / "byoai_compat.py",
)
assert spec is not None and spec.loader is not None
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)

TODAY = dt.date(2026, 12, 1)


def agent(**kw: object) -> dict:
    base: dict = {
        "id": "x",
        "name": "X",
        "channel": "T1",
        "status": "unverified",
        "verified_at": None,
        "evidence": None,
        "sources": [],
    }
    base.update(kw)
    return base


def test_verified_without_date_and_evidence_is_rejected() -> None:
    errors = bc.validate({"agents": [agent(status="verified")]})
    assert any("evidence" in e for e in errors)
    ok = agent(status="verified", verified_at="2026-11-20", evidence="docs/uat/x.md")
    assert bc.validate({"agents": [ok]}) == []


def test_unknown_status_and_duplicate_ids_are_rejected() -> None:
    errors = bc.validate({"agents": [agent(status="great"), agent()]})
    assert any("알 수 없는 등급" in e for e in errors)
    assert any("중복" in e for e in bc.validate({"agents": [agent(), agent()]}))


def test_overdue_verified_entries_are_shown_as_stale_never_upgraded() -> None:
    old = agent(status="verified", verified_at="2026-06-01", evidence="e")
    fresh = agent(id="y", status="verified", verified_at="2026-11-20", evidence="e")
    unverified = agent(id="z")
    data = {"review_every_days": 90, "agents": [old, fresh, unverified]}
    assert [a["id"] for a in bc.overdue(data, TODAY)] == ["x"]
    assert bc.effective_status(old, 90, TODAY) == "stale"
    assert (
        bc.effective_status(unverified, 90, TODAY) == "unverified"
    )  # 기한 개념이 없는 미확인을 올리지 않는다


def test_per_agent_review_days_override_the_default() -> None:
    quick = agent(status="verified", verified_at="2026-11-20", evidence="e", review_every_days=5)
    assert bc.effective_status(quick, 90, TODAY) == "stale"


def test_the_committed_table_matches_the_data_file() -> None:
    data = bc.load()
    assert bc.validate(data) == []
    current = bc.TABLE.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    assert current == bc.render(data)


def test_health_is_observed_state_separate_from_the_certified_grade() -> None:
    down = agent(health={"state": "down", "last_ok_at": "2026-12-01", "incident": None})
    assert any("incident" in e for e in bc.validate({"agents": [down]}))
    down["health"]["incident"] = "byoai-2026-12-03-x-01"
    assert bc.validate({"agents": [down]}) == []
    bad = agent(health={"state": "fine"})
    assert any("health" in e for e in bc.validate({"agents": [bad]}))
    cells = bc._cells(down, 90, TODAY)
    assert "중단" in cells and cells[2] == "미확인"  # 운영 상태가 중단이어도 등급은 그대로
