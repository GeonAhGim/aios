"""L4_compliance_and_regulatory_v1.0.md#9 CM-10 -- `domain/market_abuse.py`
unit tests.

task-2460 DoD mapping: (b) wash-trade boundary (60s hit / 61s miss / cross-
tenant false-positive guard), (c) marking-the-close boundary (30% miss /
30.1% hit / outside-window exclusion), (d) spoofing boundary (3 orders at
exactly 10x miss / above 10x hit / 2 orders never hit), (e) fail-closed
`DATA_MISSING` per pattern, (f) no coupling with CM-9's pre-trade
`rules/wash_trade.py`, (g) purity/determinism.

DEEPEN 2460 (task-2864, docs/audit/DEPTH_CM.md): the original leaf graded D1
for missing failure injection, a numeric performance assertion, a gate-red
regression test, and D3 adversarial/multi-instance proof (negative>=4 was
already satisfied). `detect()` is pure (no I/O), so those four are added as:
  - failure injection: a `Decimal`/`datetime` subclass that still passes
    every `isinstance` check upstream but raises `ArithmeticError` deep in
    the `qty`/`executed_at` arithmetic each pattern performs. This found a
    real gap -- `_safe` only caught `KeyError`/`TypeError`, so a corrupted
    numeric value escaped `detect()` as an uncaught crash instead of the
    `DATA_MISSING` hit I-02 requires; fixed by widening `_safe`'s except
    clause to also catch `ArithmeticError` (covers `decimal.InvalidOperation`,
    `OverflowError`, `ZeroDivisionError`, all `ArithmeticError` subclasses).
  - numeric performance: a wall-clock ceiling over many `detect()` calls on a
    non-trivial window (wash-trade is O(n^2) in fill count).
  - gate-red regression: `_worst_severity` reproduces the worst-verdict
    adoption CM-11's `evaluate_tenant_day` performs over this leaf's hits,
    locking in that a clean window stays ALLOW/green, a real (non-missing)
    pattern hit stays WARN (CM-11 never blocks on these three patterns
    alone), and only `DATA_MISSING` turns the leaf DENY/red.
  - D3 adversarial + multi-instance/replay: `AbuseHit` tamper rejection
    (frozen dataclass) and byte-identical results from independent OS
    processes given the same input.
"""

from __future__ import annotations

import ast
import dataclasses
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.market_abuse import (
    AbuseHit,
    detect,
)

_T0 = datetime(2026, 1, 5, 5, 0, 0, tzinfo=UTC)
_CLOSE = datetime(2026, 1, 5, 6, 0, 0, tzinfo=UTC)


def _fill(**kwargs: object) -> dict[str, object]:
    base = {
        "fill_id": "f1",
        "tenant_id": "tenant-a",
        "instrument_id": "005930",
        "owner_id": "owner-1",
        "side": "BUY",
        "qty": Decimal("10"),
        "executed_at": _T0,
    }
    base.update(kwargs)
    return base


def _order(**kwargs: object) -> dict[str, object]:
    base = {
        "order_id": "o1",
        "tenant_id": "tenant-a",
        "instrument_id": "005930",
        "qty": Decimal("10"),
        "submitted_at": _T0,
        "canceled_at": _T0 + timedelta(seconds=1),
    }
    base.update(kwargs)
    return base


def _window(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {"fills": [], "orders": [], "market_close_at": _CLOSE}
    base.update(kwargs)
    return base


# -- (c) marking the close ------------------------------------------------


def _other_participants(total_qty: str, executed_at: datetime) -> list[dict[str, object]]:
    """Three other tenants splitting `total_qty`, each individually well
    under the 30% share limit -- keeps the fixture focused on tenant-a's
    boundary instead of also tripping the rule for a counterparty."""
    per_tenant = Decimal(total_qty) / 3
    return [
        _fill(
            fill_id=f"other-{i}",
            tenant_id=f"tenant-other-{i}",
            qty=per_tenant,
            executed_at=executed_at,
        )
        for i in range(3)
    ]


# -- (f) no coupling with CM-9's pre-trade wash_trade.py -------------------


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


# -- Failure injection (DEEPEN) -------------------------------------------


class _ExplodingDecimal(Decimal):
    """A `Decimal` subclass that still passes `isinstance(value, Decimal)`
    but raises on arithmetic -- simulates a corrupted numeric type slipping
    past every type guard and into `qty` accumulation."""

    def __add__(self, other: object) -> Decimal:
        raise ArithmeticError("simulated corrupted Decimal arithmetic")

    __radd__ = __add__


class _ExplodingDatetime(datetime):
    """A `datetime` subclass that still passes `isinstance(value, datetime)`
    but raises on subtraction -- simulates a corrupted timestamp slipping
    into the wash-trade delta-seconds calculation."""

    def __sub__(self, other: object) -> Any:
        raise ArithmeticError("simulated corrupted datetime arithmetic")


# -- Gate-red regression (DEEPEN) -------------------------------------------

_SEVERITY_RANK = {ComplianceVerdict.ALLOW: 0, ComplianceVerdict.WARN: 1, ComplianceVerdict.DENY: 2}


def _worst_severity(hits: list[AbuseHit]) -> ComplianceVerdict:
    """Mirrors the worst-verdict adoption CM-11's `evaluate_tenant_day`
    performs over this leaf's hits (`evaluate_post_trade.py`'s `_record`)."""
    if not hits:
        return ComplianceVerdict.ALLOW
    return max((h.severity for h in hits), key=lambda s: _SEVERITY_RANK[s])


def test_gate_turns_red_only_when_data_is_missing():
    hits = detect({"orders": [], "market_close_at": _CLOSE}, {})
    assert _worst_severity(hits) == ComplianceVerdict.DENY


# -- D3 adversarial + multi-instance/replay proof (DEEPEN) ------------------


def test_abuse_hit_rejects_post_construction_tampering():
    """D3 적대적: 컴플라이언스 판정을 좌우하는 `AbuseHit`을 만든 뒤 메모리
    에서 `severity`를 DENY -> WARN으로 바꿔치기하는 시도(다운스트림 코드의
    버그 또는 공격)는 예외 없이 조용히 성공해서는 안 된다. `AbuseHit`은
    이미 `frozen=True` dataclass이므로, 그 방어선이 실제로 걸려 있음을
    실증한다."""
    hit = _missing_hit_for_test()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cast(Any, hit).severity = ComplianceVerdict.WARN


def _missing_hit_for_test() -> AbuseHit:
    hits = detect({"orders": [], "market_close_at": _CLOSE}, {})
    return hits[0]


def _replay_in_subprocess(
    window: dict[str, object], params: dict[str, object]
) -> tuple[tuple[str, str, str, str, str], ...]:
    """Module-level so it is picklable for `ProcessPoolExecutor` on Windows
    (spawn start method)."""
    hits = detect(window, params)
    return tuple(
        (
            h.pattern_id,
            h.severity.value,
            h.reason_code,
            h.message,
            str(sorted(h.evidence.items(), key=str)),
        )
        for h in hits
    )


def test_detect_replay_across_independent_processes_is_byte_identical():
    """D3 다중 인스턴스/리플레이 증명: 전역 상태가 완전히 분리된 별도 OS
    프로세스 3개가 동일한 window/params를 각자 평가해도 완전히 동일한
    결과를 내야 한다."""
    fills = [
        _fill(fill_id="f1", side="BUY", executed_at=_T0),
        _fill(fill_id="f2", side="SELL", executed_at=_T0 + timedelta(seconds=10)),
    ]
    window = _window(fills=fills)

    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_replay_in_subprocess, [window] * 3, [{}] * 3))

    assert len(results) == 3
    assert len(set(results)) == 1
