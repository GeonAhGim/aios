"""J8 trail aggregation and legacy API compatibility."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.decision_trail import to_decision_trail_response
from src.services.decision_trail import TrailEntry, TrailKind, build_decision_trail
from src.services.decision_trail_service import DecisionTrailService

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def audit(data=None, action="execution.converted_to_live", at=NOW):
    return {
        "action_type": action,
        "actor_agent": "human",
        "created_at": at,
        "decision_data": data if data is not None else {},
    }


def test_chronological_order_and_ties():
    rows = [audit({}, "execution.exposure_expanded"), audit(), audit(at=NOW - timedelta(seconds=1))]
    risk = {
        "evaluated_at": NOW,
        "decision_id": "risk-1",
        "gate_kind": "PRE_TRADE",
        "outcome": "DENY",
        "reason_codes": ["LIMIT"],
        "rule_version": "v1",
    }
    entries = build_decision_trail(rows, [risk])
    assert [e.kind for e in entries] == [
        TrailKind.CONVERSION,
        TrailKind.CONVERSION,
        TrailKind.EXPANSION,
        TrailKind.RISK_DECISION,
    ]
    assert entries[-1].summary == risk
    assert entries[-1].actor is None
    assert entries[0].actor == "human"


def test_complete_conversion_and_expansion():
    conversion = dict(
        strategy_id=1,
        strategy_version="v2",
        new_execution_id=3,
        target_capital=Decimal("100.10"),
        allocated_capital=Decimal("10.01"),
        max_loss_amount=Decimal("1"),
        initial_exposure_pct=10,
        max_drawdown_pct=10,
    )
    expansion = dict(
        allocated_capital_before=10,
        allocated_capital_after=20,
        exposure_pct_after=20,
        max_drawdown_pct_after=5,
    )
    entries = build_decision_trail(
        [
            audit({**conversion, "readiness_evidence": {"ready": False}}),
            audit(expansion, "execution.exposure_expanded"),
        ],
        [],
    )
    assert entries[0].summary == conversion
    assert entries[0].evidence == {"ready": False}
    assert entries[1].summary == expansion
    assert all(not e.unavailable for e in entries)


def test_missing_keys_are_not_zero():
    entry = build_decision_trail([audit({"allocated_capital": 0})], [])[0]
    assert entry.summary["target_capital"] is None
    assert entry.unavailable["target_capital"] == "기록 없음"
    assert entry.summary["allocated_capital"] == 0
    assert "allocated_capital" not in entry.unavailable


def test_missing_risk_fields():
    entry = build_decision_trail([], [{"evaluated_at": NOW}])[0]
    assert entry.summary["outcome"] is None
    assert entry.unavailable["outcome"] == "기록 없음"
    assert entry.evidence is None


def test_json_string_preserves_money():
    entry = build_decision_trail(
        [audit('{"target_capital": 123.4567890123456789, "readiness_evidence": {"count": 2}}')], []
    )[0]
    assert entry.summary["target_capital"] == Decimal("123.4567890123456789")
    assert entry.evidence == {"count": 2}


@pytest.mark.parametrize("data", ['{"broken":', "[]", "42", '"text"'])
def test_corrupt_payload_is_retained(data):
    entries = build_decision_trail([audit(data), audit()], [])
    assert len(entries) == 2
    assert entries[0].unavailable["decision_data"] == "파싱 실패"
    assert entries[0].summary["target_capital"] is None


def test_unknown_action_needs_no_timestamp():
    assert build_decision_trail([{"action_type": "other", "decision_data": "{"}], []) == []
    assert build_decision_trail([], []) == []


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        build_decision_trail([audit(at=NOW.replace(tzinfo=None))], [])


def test_offset_normalized_and_model_frozen():
    entry = build_decision_trail([audit(at=NOW.astimezone(timezone(timedelta(hours=9))))], [])[0]
    assert entry.occurred_at == NOW
    assert entry.occurred_at.tzinfo == UTC
    with pytest.raises(ValidationError, match="frozen"):
        entry.actor = "other"


def test_no_input_or_default_aliasing():
    evidence = {"nested": [1]}
    rows = [audit({"readiness_evidence": evidence})]
    first, second = build_decision_trail(rows * 2, [])
    first.evidence["nested"].append(2)
    assert evidence == {"nested": [1]}
    assert second.evidence == evidence
    a = TrailEntry(
        kind=TrailKind.CONVERSION, occurred_at=NOW, actor=None, summary={}, evidence=None
    )
    b = TrailEntry(
        kind=TrailKind.CONVERSION, occurred_at=NOW, actor=None, summary={}, evidence=None
    )
    a.unavailable["test"] = "missing"
    assert not b.unavailable


@pytest.mark.asyncio
async def test_legacy_service_response_unchanged():
    user_id = uuid4()
    execution_id = 7
    row = dict(
        decision_id=uuid4(),
        gate_kind="PRE_TRADE",
        outcome="DENY",
        reason_codes=["LIMIT"],
        rule_version="v1",
        evaluated_at=NOW,
        expires_at=NOW + timedelta(seconds=10),
        trace_id=uuid4(),
        summary="limit",
    )

    class Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def fetchrow(self, sql, *args):
            return {"user_id": user_id, "mode": "paper"}

        async def fetch(self, sql, *args):
            if "risk_decision" in sql:
                return [row]
            return []

    class Pool:
        def acquire(self):
            return Conn()

    entries = await DecisionTrailService(Pool()).get_trail(execution_id, user_id)
    response = to_decision_trail_response(entries).entries[0]
    assert response.kind == "RISK_DECISION"
    assert response.summary["decision_id"] == row["decision_id"]
    assert response.summary["outcome"] == "DENY"
    assert response.summary["reason_codes"] == ["LIMIT"]
    assert response.summary["rule_version"] == "v1"


def test_empty_entries_produces_empty_response():
    response = to_decision_trail_response([])
    assert response.entries == []


def test_conversion_entry_maps_safe_defaults_for_risk_fields():
    """CONVERSION/EXPANSION entries lack risk fields — mapper must not raise."""
    entry = TrailEntry(
        kind=TrailKind.CONVERSION,
        occurred_at=NOW,
        actor="human",
        summary={"strategy_id": 1},
        evidence=None,
    )
    response = to_decision_trail_response([entry]).entries[0]
    assert response.kind == "CONVERSION"
    assert response.actor == "human"
    assert response.summary == {"strategy_id": 1}
    assert response.evidence is None


def test_mixed_conversion_and_risk_entries():
    """Mixed trail: conversion first, risk decision second — all map cleanly."""
    conversion = TrailEntry(
        kind=TrailKind.CONVERSION,
        occurred_at=NOW - timedelta(seconds=1),
        actor="agent",
        summary={"target_capital": Decimal("1000")},
        evidence=None,
    )
    risk = TrailEntry(
        kind=TrailKind.RISK_DECISION,
        occurred_at=NOW,
        actor=None,
        summary={
            "decision_id": uuid4(),
            "outcome": "PASS",
            "reason_codes": ["SINGLE"],
            "rule_version": "v2",
            "evaluated_at": NOW,
            "expires_at": NOW + timedelta(seconds=30),
            "trace_id": uuid4(),
            "summary": "all clear",
        },
        evidence=None,
    )
    response = to_decision_trail_response([conversion, risk]).entries
    assert len(response) == 2
    assert response[0].kind == "CONVERSION"
    assert response[1].kind == "RISK_DECISION"
    assert response[0].actor == "agent"
    assert response[1].summary["outcome"] == "PASS"
    assert response[1].summary["trace_id"] is not None


@pytest.mark.perf
def test_5000_entries_within_query_budget(perf_budget):
    # ADR-2026-09-09-C: use the 5k-row query budget of 200 ms.
    rows = [
        {
            "evaluated_at": NOW,
            "decision_id": index,
            "gate_kind": "PRE_TRADE",
            "outcome": "DENY",
            "reason_codes": ["LIMIT"],
            "rule_version": "v1",
        }
        for index in range(5000)
    ]
    perf_budget.assert_within(lambda: build_decision_trail([], rows), budget_ms=200)
    assert len(build_decision_trail([], rows)) == 5000
