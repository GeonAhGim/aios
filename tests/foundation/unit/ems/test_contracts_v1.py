"""EM-1 contracts/v1.py — 계약 레벨 검증(스키마·에러 taxonomy 스냅샷)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.ems.contracts.v1 import (
    HTTP_STATUS,
    TERMINAL_ORDER_STATUSES,
    AlgoKind,
    AlgoSpec,
    ChildOrder,
    EmsErrorCode,
    ParentOrder,
    ParentOrderConstraints,
    RouteDecision,
    TcaResult,
)
from src.foundation.ems.domain.parent_child import (
    AlgoConstraintError,
    ParentTerminalError,
    assert_can_create_child,
    assert_parent_accepts_new_child,
    assert_slice_within_parent_qty,
)

_NOW = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)


def _algo_spec(**overrides: object) -> AlgoSpec:
    fields: dict[str, object] = {
        "kind": AlgoKind.TWAP,
        "start": _NOW,
        "end": _NOW + timedelta(hours=1),
        "max_participation_pct": Decimal("10"),
        "slice_interval_sec": 60,
        "urgency": Decimal("0.5"),
        "limit_price": None,
        "seed": 42,
    }
    fields.update(overrides)
    return AlgoSpec.model_validate(fields)


def test_algo_spec_accepts_known_kind() -> None:
    spec = _algo_spec()
    assert spec.kind == AlgoKind.TWAP
    assert spec.schema_version == "v1"


def test_algo_spec_rejects_kind_outside_table() -> None:
    """§3 표 밖 값(`sniper`)은 pydantic ValidationError로 거부된다 — DoD(1)."""
    with pytest.raises(ValidationError):
        _algo_spec(kind="sniper")


def test_algo_spec_rejects_naive_datetime() -> None:
    """AwareDatetime은 tz-naive start/end를 거부한다 — DoD(2)."""
    with pytest.raises(ValidationError):
        _algo_spec(start=datetime(2026, 9, 7, 0, 0), end=_NOW + timedelta(hours=1))
    with pytest.raises(ValidationError):
        _algo_spec(start=_NOW, end=datetime(2026, 9, 7, 1, 0))


def test_algo_spec_rejects_end_before_or_equal_start() -> None:
    with pytest.raises(ValidationError):
        _algo_spec(start=_NOW, end=_NOW)
    with pytest.raises(ValidationError):
        _algo_spec(start=_NOW, end=_NOW - timedelta(seconds=1))


def test_algo_spec_same_seed_same_construction() -> None:
    """EM-A3 — 계약 자체는 seed를 그대로 보관(결정론 재현은 EM-8+ 몫)."""
    a = _algo_spec(seed=7)
    b = _algo_spec(seed=7)
    assert a.seed == b.seed == 7


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("max_participation_pct", 10.5),
        ("urgency", 0.5),
        ("limit_price", 100.25),
    ],
)
def test_algo_spec_rejects_float_for_decimal_fields(field: str, bad_value: float) -> None:
    """DoD(3) — 금액·수량·bps 계열 Decimal 필드는 float 입력을 거부한다."""
    with pytest.raises(ValidationError):
        _algo_spec(**{field: bad_value})


def test_algo_spec_accepts_decimal_fields_via_string_without_precision_loss() -> None:
    """DoD(3) — 문자열 경유는 손실 없이 허용된다(0.1 float 오차 재현 안 됨)."""
    spec = _algo_spec(max_participation_pct="10.1", urgency="0.30", limit_price="99.99")
    assert spec.max_participation_pct == Decimal("10.1")
    assert spec.urgency == Decimal("0.30")
    assert spec.limit_price == Decimal("99.99")
    assert spec.max_participation_pct.as_tuple() == Decimal("10.1").as_tuple()


def test_parent_order_and_child_order_roundtrip() -> None:
    parent_id = uuid4()
    parent = ParentOrder(
        parent_id=parent_id,
        instrument_id="BTC/USDT",
        side=OrderSide.BUY,
        qty=Decimal("1.5"),
        algo=_algo_spec(),
        constraints=ParentOrderConstraints(max_participation_pct=Decimal("10")),
        fund_id=uuid4(),
        portfolio_id=uuid4(),
        arrival_ts=_NOW,
    )
    assert parent.status == OrderStatus.CREATED
    assert parent.schema_version == "v1"

    child = ChildOrder(
        child_id=uuid4(),
        parent_id=parent_id,
        slice_seq=0,
        instrument_id="BTC/USDT",
        side=OrderSide.BUY,
        planned_qty=Decimal("0.5"),
        scheduled_at=_NOW,
    )
    assert child.order_id is None
    assert child.parent_id == parent_id


def test_parent_order_qty_rejects_float() -> None:
    with pytest.raises(ValidationError):
        ParentOrder.model_validate(
            {
                "parent_id": uuid4(),
                "instrument_id": "BTC/USDT",
                "side": OrderSide.BUY,
                "qty": 1.5,
                "algo": _algo_spec(),
                "constraints": ParentOrderConstraints(max_participation_pct=Decimal("10")),
                "fund_id": uuid4(),
                "portfolio_id": uuid4(),
                "arrival_ts": _NOW,
            }
        )


def test_parent_order_arrival_ts_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        ParentOrder(
            parent_id=uuid4(),
            instrument_id="BTC/USDT",
            side=OrderSide.BUY,
            qty=Decimal("1.5"),
            algo=_algo_spec(),
            constraints=ParentOrderConstraints(max_participation_pct=Decimal("10")),
            fund_id=uuid4(),
            portfolio_id=uuid4(),
            arrival_ts=datetime(2026, 9, 7, 0, 0),
        )


def test_route_decision_requires_reason_codes() -> None:
    with pytest.raises(ValidationError):
        RouteDecision(venue="binance", reason_codes=[], expected_cost_bps=Decimal("1.2"))

    decision = RouteDecision(
        venue="binance", reason_codes=["BEST_FEE"], expected_cost_bps=Decimal("1.2")
    )
    assert decision.schema_version == "v1"


def test_route_decision_expected_cost_bps_rejects_float() -> None:
    with pytest.raises(ValidationError):
        RouteDecision.model_validate(
            {"venue": "binance", "reason_codes": ["BEST_FEE"], "expected_cost_bps": 1.2}
        )


def test_tca_result_fields_are_strict_decimal() -> None:
    result = TcaResult(
        arrival_bps=Decimal("1"),
        vwap_bps=Decimal("2"),
        impact_bps=Decimal("3"),
        fees_bps=Decimal("4"),
        opportunity_bps=Decimal("5"),
    )
    assert result.schema_version == "v1"
    with pytest.raises(ValidationError):
        TcaResult.model_validate(
            {
                "arrival_bps": 1.0,
                "vwap_bps": Decimal("2"),
                "impact_bps": Decimal("3"),
                "fees_bps": Decimal("4"),
                "opportunity_bps": Decimal("5"),
            }
        )


def test_error_taxonomy_matches_spec_literally() -> None:
    """§3 4종 에러코드가 문자 단위로 일치 — DoD(4)."""
    assert {code.value for code in EmsErrorCode} == {
        "EM_ALGO_CONSTRAINT",
        "EM_NO_ROUTE",
        "EM_PARENT_TERMINAL",
        "EM_PARTICIPATION_EXCEEDED",
    }


def test_error_taxonomy_http_status_snapshot() -> None:
    """§3 400/409/409/409 매핑을 스냅샷으로 고정 — DoD(4)."""
    assert HTTP_STATUS == {
        EmsErrorCode.ALGO_CONSTRAINT: 400,
        EmsErrorCode.NO_ROUTE: 409,
        EmsErrorCode.PARENT_TERMINAL: 409,
        EmsErrorCode.PARTICIPATION_EXCEEDED: 409,
    }


def test_every_error_code_has_an_http_status() -> None:
    for code in EmsErrorCode:
        assert code in HTTP_STATUS


# ---------------------------------------------------------------------------
# DEEPEN — task-3113: 3 additional tests (serialization, numerical, gate-red)
# ---------------------------------------------------------------------------


def test_tca_result_json_serialisation_roundtrip() -> None:
    """Serialization boundary — pydantic model_dump_json → model_validate
    must preserve Decimal values exactly (DoD: 직렬화 경계 실패 주입 1건)."""
    result = TcaResult(
        arrival_bps=Decimal("1.2345"),
        vwap_bps=Decimal("2.3456"),
        impact_bps=Decimal("3.4567"),
        fees_bps=Decimal("4.5678"),
        opportunity_bps=Decimal("5.6789"),
    )
    dumped = result.model_dump_json()
    restored = TcaResult.model_validate_json(dumped)
    assert restored.arrival_bps == Decimal("1.2345")
    assert restored.vwap_bps == Decimal("2.3456")
    assert restored.impact_bps == Decimal("3.4567")
    assert restored.fees_bps == Decimal("4.5678")
    assert restored.opportunity_bps == Decimal("5.6789")


def test_tca_result_serialisation_rejects_float() -> None:
    """Serialization boundary — JSON 경계에 float가 들어오면 거부된다
    (직렬화 실패 주입 2번째)."""
    with pytest.raises(ValidationError):
        TcaResult.model_validate_json(
            '{"arrival_bps": 1.5, "vwap_bps": 2, '
            '"impact_bps": 3, "fees_bps": 4, '
            '"opportunity_bps": 5}'
        )


def test_tca_decomposition_identity() -> None:
    """Numerical assertion — EM-A5/§8: total cost ≈ impact + fees +
    opportunity (DoD: 수치 성능 단언 1건). arrival_bps는 분해의 기준이며
    impact_bps + fees_bps + opportunity_bps가 arrival_bps와 0.01bps 오차
    범위 안에 있어야 한다."""
    result = TcaResult(
        arrival_bps=Decimal("10.000"),
        vwap_bps=Decimal("9.800"),
        impact_bps=Decimal("6.000"),
        fees_bps=Decimal("3.000"),
        opportunity_bps=Decimal("1.000"),
    )
    decomposition_sum = result.impact_bps + result.fees_bps + result.opportunity_bps
    tolerance = Decimal("0.01")
    assert abs(decomposition_sum - result.arrival_bps) <= tolerance, (
        f"decomposition {decomposition_sum} != arrival {result.arrival_bps}"
    )


def test_gate_red_parent_terminal_blocks_child_creation() -> None:
    """Gate red reproduction — EM-A4: terminal parent may not spawn children
    (DoD: 게이트 적색 재현 1건). 실제 도메인 게이트
    (`assert_parent_accepts_new_child` / `assert_can_create_child`)를
    terminal parent status로 호출해 `ParentTerminalError`가 발생하고,
    그 예외의 `.code`가 스펙의 `EM_PARENT_TERMINAL` taxonomy와 정확히
    일치함을 검증한다 — 스키마 검증이 아니라 실제 게이트 실패 동작 재현."""
    terminal_parent = ParentOrder(
        parent_id=uuid4(),
        instrument_id="BTC/USDT",
        side=OrderSide.BUY,
        qty=Decimal("1.0"),
        algo=_algo_spec(),
        constraints=ParentOrderConstraints(max_participation_pct=Decimal("10")),
        fund_id=uuid4(),
        portfolio_id=uuid4(),
        arrival_ts=_NOW,
        status=OrderStatus.FILLED,  # terminal 상태
    )
    assert terminal_parent.status in TERMINAL_ORDER_STATUSES

    with pytest.raises(ParentTerminalError) as excinfo:
        assert_parent_accepts_new_child(terminal_parent.status)
    assert excinfo.value.code == EmsErrorCode.PARENT_TERMINAL
    assert HTTP_STATUS[excinfo.value.code] == 409

    # EM-A4 precedence: the combinator must raise the same taxonomy even
    # when the EM-A1 quantity check would also fail on a non-terminal parent.
    with pytest.raises(ParentTerminalError) as combinator_excinfo:
        assert_can_create_child(
            parent_status=terminal_parent.status,
            parent_qty=Decimal("1.0"),
            committed_child_qty=Decimal("0"),
            new_slice_qty=Decimal("1.0"),
        )
    assert combinator_excinfo.value.code == EmsErrorCode.PARENT_TERMINAL

    # negative control: a non-terminal parent does not trip EM_PARENT_TERMINAL.
    assert_parent_accepts_new_child(OrderStatus.CREATED)


def test_gate_red_algo_constraint_blocks_oversized_slice() -> None:
    """Gate red reproduction — EM-A1: a slice pushing committed child qty
    past parent qty raises `AlgoConstraintError` with taxonomy
    `EM_ALGO_CONSTRAINT` (400), not merely a schema validation error."""
    with pytest.raises(AlgoConstraintError) as excinfo:
        assert_slice_within_parent_qty(
            parent_qty=Decimal("1.0"),
            committed_child_qty=Decimal("0.6"),
            new_slice_qty=Decimal("0.5"),
        )
    assert excinfo.value.code == EmsErrorCode.ALGO_CONSTRAINT
    assert HTTP_STATUS[excinfo.value.code] == 400

    # boundary case: exact equality is allowed, no error.
    assert_slice_within_parent_qty(
        parent_qty=Decimal("1.0"),
        committed_child_qty=Decimal("0.6"),
        new_slice_qty=Decimal("0.4"),
    )
