"""decision-trail 응답 변환 -- TrailEntry의 정보를 잃지 않는다 (J8 trail)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from src.api.schemas.decision_trail import DecisionTrailResponse, to_decision_trail_response
from src.services.decision_trail import TrailEntry, TrailKind

T0 = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)


def _entry(kind, **kw):
    base = {
        "kind": kind,
        "occurred_at": T0,
        "actor": None,
        "summary": {},
        "evidence": None,
        "unavailable": {},
    }
    base.update(kw)
    return TrailEntry(**base)


def test_conversion_entry_keeps_actor_summary_and_evidence():
    entry = _entry(
        TrailKind.CONVERSION,
        actor="user-1",
        summary={"strategy_id": "s1", "target_capital": Decimal("500"), "max_loss_amount": "25"},
        evidence={"fill_ratio": "0.8", "net_pnl": None},
        unavailable={"net_pnl": "원천 데이터 없음"},
    )
    out = to_decision_trail_response([entry]).entries[0]
    assert out.kind == "CONVERSION" and out.actor == "user-1"
    assert out.summary["strategy_id"] == "s1" and out.summary["target_capital"] == Decimal("500")
    assert out.evidence == {"fill_ratio": "0.8", "net_pnl": None}
    assert out.unavailable == {"net_pnl": "원천 데이터 없음"}


def test_missing_values_stay_unavailable_not_zero_filled():
    entry = _entry(
        TrailKind.EXPANSION,
        summary={"allocated_capital_before": Decimal("50"), "allocated_capital_after": None},
        unavailable={"allocated_capital_after": "기록 없음"},
    )
    out = to_decision_trail_response([entry]).entries[0]
    assert out.summary["allocated_capital_after"] is None
    assert out.unavailable["allocated_capital_after"] == "기록 없음"
    assert out.evidence is None


def test_risk_decision_entry_and_order_are_preserved():
    risk = _entry(
        TrailKind.RISK_DECISION,
        summary={"decision_id": uuid4(), "outcome": "ALLOW", "reason_codes": ["OK"]},
    )
    conv = _entry(TrailKind.CONVERSION, actor="u")
    out = to_decision_trail_response([conv, risk])
    assert [e.kind for e in out.entries] == ["CONVERSION", "RISK_DECISION"]
    assert out.entries[1].summary["outcome"] == "ALLOW"


def test_empty_trail_and_response_is_isolated_from_the_source():
    assert to_decision_trail_response([]) == DecisionTrailResponse(entries=[])
    summary = {"k": 1}
    entry = _entry(TrailKind.EXPANSION, summary=summary)
    out = to_decision_trail_response([entry])
    out.entries[0].summary["k"] = 2
    assert entry.summary == {"k": 1}
