"""L4_compliance_and_regulatory_v1.0.md#9 CM-9 — `domain/rules/wash_trade.py`
unit tests.

task-2458 DoD mapping: (c) same-tenant crossing-price DENY + no-cross ALLOW
+ cross-tenant negative (no false positive), (d) missing `open_orders` is
fail-closed DENY, (e) static purity/determinism.

DEEPEN 2863 (task-2863, docs/audit/DEPTH_CM.md): the original CM-9 leaf
graded D1 for missing failure injection, a numeric performance assertion, a
gate-red reproduction, and D3 adversarial/multi-instance proof (negative≥6
was already satisfied). `check()` is pure (no I/O), so those four are added
as:
  - failure injection: a `Decimal` subclass whose `__le__`/`__ge__` raise
    makes `_crosses`'s price comparison raise inside `check()`; wired
    through the real `RuleBundle`/`evaluate_bundle` gate (not `check()`
    called directly), this proves `_run_rule`'s fail-closed DENY (CM-A2)
    actually covers this rule — a corrupted numeric type that still passes
    the `isinstance(value, Decimal)` guard must never silently ALLOW.
  - numeric performance: a wall-clock ceiling on 10,000 evaluations.
  - gate-red reproduction: the same `RuleBundle`/`evaluate_bundle` pipeline,
    run once with a crossing order (decision.verdict must turn DENY/red)
    and once with a non-crossing order (decision.verdict must stay
    ALLOW/green).
  - D3 adversarial + multi-instance/replay: `RuleHit` tamper rejection
    (frozen pydantic model) and byte-identical results from independent OS
    processes given the same input.
"""

from __future__ import annotations

import ast
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules import short_sale, wash_trade

_FORBIDDEN_IMPORTS = ("datetime", "random", "httpx", "asyncpg", "openai")
_NOW = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)

_TENANT_A = "tenant-a"
_TENANT_B = "tenant-b"
_INSTRUMENT = "BTC/USDT"


class _ExplodingDecimal(Decimal):
    """A `Decimal` subclass that still passes `isinstance(value, Decimal)`
    but raises on the comparisons `_crosses` performs — simulates a
    corrupted numeric type slipping past `wash_trade._decimal_or_none`'s
    type guard."""

    def __le__(self, other: object) -> bool:
        raise ArithmeticError("simulated corrupted Decimal comparison")

    def __ge__(self, other: object) -> bool:
        raise ArithmeticError("simulated corrupted Decimal comparison")


def _open_order(tenant_id: str, side: str, price: Decimal, instrument: str = _INSTRUMENT) -> dict:
    return {"tenant_id": tenant_id, "instrument": instrument, "side": side, "price": price}


def test_crossing_opposite_order_same_tenant_denies():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }
    hit = wash_trade.check({}, snapshot)
    assert hit is not None
    assert hit.rule_id == wash_trade.RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["tenant_id"] == _TENANT_A


def test_non_crossing_opposite_order_same_tenant_passes():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10010"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }
    assert wash_trade.check({}, snapshot) is None


def test_crossing_opposite_order_different_tenant_passes():
    """교차 테넌트 오탐 방지 — 서로 다른 tenant_id의 반대 주문은 자전거래가
    아니다(진짜 상대방)."""
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_B, "BUY", Decimal("10000"))],
    }
    assert wash_trade.check({}, snapshot) is None


def test_same_side_open_order_is_never_a_wash_trade():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "SELL", Decimal("10000"))],
    }
    assert wash_trade.check({}, snapshot) is None


def test_different_instrument_is_never_a_wash_trade():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"), instrument="ETH/USDT")],
    }
    assert wash_trade.check({}, snapshot) is None


def test_missing_open_orders_is_fail_closed_deny():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("1"),
    }
    hit = wash_trade.check({}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence["missing_field"] == "snapshot.open_orders"


def test_missing_tenant_id_is_fail_closed_deny():
    snapshot = {
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("1"),
        "open_orders": [],
    }
    hit = wash_trade.check({}, snapshot)
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_check_is_pure_and_deterministic():
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }
    assert wash_trade.check({}, snapshot) == wash_trade.check({}, snapshot)


def test_short_sale_and_wash_trade_register_in_a_bundle_and_both_evaluate():
    """CM-9 (f) — wiring here stops at registering both new rules in a
    `RuleBundle`; `application/evaluate_pre_trade.py` (CM-8) and order paths
    are untouched by this leaf. This proves `evaluate_bundle` actually
    invokes both registered `rule_id`s (both hit, given inputs crafted to
    trigger each) rather than asserting on production wiring that does not
    exist yet."""
    bundle = RuleBundle(
        version="v1",
        rules=(
            RuleSpec(rule_id=short_sale.RULE_ID, params={}, check=short_sale.check),
            RuleSpec(rule_id=wash_trade.RULE_ID, params={}, check=wash_trade.check),
        ),
    )
    snapshot = {
        # triggers short_sale: naked short (excess 1, no locate)
        "side": "SELL",
        "order_qty": Decimal("101"),
        "position_qty": Decimal("100"),
        "borrow_available_qty": Decimal("0"),
        # triggers wash_trade: crosses own open buy at the same price
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }

    decision = evaluate_bundle(bundle, snapshot, now=_NOW)

    hit_rule_ids = {hit.rule_id for hit in decision.rule_hits}
    assert hit_rule_ids == {short_sale.RULE_ID, wash_trade.RULE_ID}
    assert decision.verdict == ComplianceVerdict.DENY


def test_module_imports_no_clock_random_or_io_libraries():
    """CM-A2 — static proof that this rule cannot be non-deterministic or
    perform I/O: no `datetime`/`random`/`httpx`/`asyncpg`/`openai` import."""
    source = Path(wash_trade.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ] + [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    for forbidden in _FORBIDDEN_IMPORTS:
        assert not any(
            module == forbidden or module.startswith(forbidden + ".") for module in imported_modules
        ), f"unexpected non-deterministic/IO import: {forbidden}"


# --- Failure injection (DEEPEN) ----------------------------------------------


def _bundle_of(params: dict[str, Any]) -> RuleBundle:
    return RuleBundle(
        version="test",
        rules=(RuleSpec(rule_id=wash_trade.RULE_ID, params=params, check=wash_trade.check),),
    )


def test_corrupted_decimal_comparison_fails_closed_through_the_gate() -> None:
    """실패 주입: `snapshot["order_price"]`가 `isinstance(..., Decimal)`은
    통과하지만 `_crosses`의 비교 연산에서 예외를 던지는 손상된 서브클래스면
    `check()` 자신이 아니라 실제 게이트(`RuleBundle` + `evaluate_bundle`)가
    fail-closed DENY로 바꾸는지 증명한다(CM-A2)."""
    bundle = _bundle_of({})
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": _ExplodingDecimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }
    decision = evaluate_bundle(bundle, snapshot, now=_NOW)

    assert decision.verdict == ComplianceVerdict.DENY
    assert len(decision.rule_hits) == 1
    assert decision.rule_hits[0].rule_id == wash_trade.RULE_ID
    assert decision.rule_hits[0].severity == ComplianceVerdict.DENY


# --- Numeric performance (DEEPEN) -------------------------------------------


def test_check_meets_latency_budget_over_many_calls() -> None:
    """수치 성능 단언: 10,000회 반복 호출의 총 지연이 넉넉한 상한(3.0s,
    호출당 평균 300us) 안에 들어야 한다.

    QA(task-2472): 원래 1.0s(100us/call) 예산은 같은 파일의 앞선
    `ProcessPoolExecutor` 리플레이 테스트가 만든 프로세스 기동/스케줄링
    잔여 부하 아래서 재현 가능하게 깜빡였다(단독 실행 시 통과, 파일 전체
    또는 짝 파일과 함께 실행 시 1.1~1.5s로 초과). 실제 회귀는 여전히
    몇 배 수준으로 잡히므로 정상 부하에서 깜빡이지 않도록 3.0s로 넓힌다."""
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }

    started = time.perf_counter()
    for _ in range(10_000):
        wash_trade.check({}, snapshot)
    elapsed_s = time.perf_counter() - started

    assert elapsed_s < 3.0, f"10,000 evaluations took {elapsed_s:.3f}s (budget 3.0s)"


# --- Gate-red reproduction (DEEPEN) ------------------------------------------


def test_crossing_order_turns_the_gate_red_and_non_crossing_keeps_it_green() -> None:
    """게이트 적색 재현: `check()`를 단독 호출하는 것과 별개로, 실제 규칙
    평가 파이프라인(`RuleBundle` + `evaluate_bundle`)에 이 규칙 하나만
    실어 돌렸을 때 자전거래 교차 주문이면 전체 결정이 DENY(적색)로,
    비교차 주문이면 ALLOW(녹색)로 정확히 갈리는지 증명한다."""
    bundle = _bundle_of({})

    red = evaluate_bundle(
        bundle,
        {
            "tenant_id": _TENANT_A,
            "instrument": _INSTRUMENT,
            "side": "SELL",
            "order_price": Decimal("10000"),
            "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
        },
        now=_NOW,
    )
    assert red.verdict == ComplianceVerdict.DENY

    green = evaluate_bundle(
        bundle,
        {
            "tenant_id": _TENANT_A,
            "instrument": _INSTRUMENT,
            "side": "SELL",
            "order_price": Decimal("10010"),
            "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
        },
        now=_NOW,
    )
    assert green.verdict == ComplianceVerdict.ALLOW


# --- D3 adversarial + multi-instance/replay proof (DEEPEN) ------------------


def test_rule_hit_rejects_post_construction_tampering() -> None:
    """D3 적대적: 컴플라이언스 판정을 좌우하는 `RuleHit`을 만든 뒤 메모리
    에서 `severity`를 DENY -> ALLOW로 바꿔치기하는 시도(다운스트림 코드의
    버그 또는 공격)는 예외 없이 조용히 성공해서는 안 된다. `RuleHit`은 이미
    `frozen=True` pydantic 모델이므로(I-09), 그 방어선이 실제로 걸려
    있음을 실증한다."""
    hit = wash_trade.check(
        {},
        {
            "tenant_id": _TENANT_A,
            "instrument": _INSTRUMENT,
            "side": "SELL",
            "order_price": Decimal("10000"),
            "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
        },
    )
    assert hit is not None
    with pytest.raises(ValidationError):
        hit.severity = ComplianceVerdict.ALLOW  # type: ignore[misc]


def _replay_in_subprocess(params: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, str, str]:
    """Module-level so it is picklable for `ProcessPoolExecutor` on Windows
    (spawn start method)."""
    hit = wash_trade.check(params, snapshot)
    assert hit is not None
    return (hit.rule_id, hit.severity.value, str(hit.evidence))


def test_replay_across_independent_processes_is_byte_identical() -> None:
    """D3 다중 인스턴스/리플레이 증명: 전역 상태가 완전히 분리된 별도 OS
    프로세스 3개가 동일한 params/snapshot을 각자 평가해도 완전히 동일한
    (rule_id, severity, evidence)를 내야 한다."""
    params: dict[str, Any] = {}
    snapshot = {
        "tenant_id": _TENANT_A,
        "instrument": _INSTRUMENT,
        "side": "SELL",
        "order_price": Decimal("10000"),
        "open_orders": [_open_order(_TENANT_A, "BUY", Decimal("10000"))],
    }

    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_replay_in_subprocess, [params] * 3, [snapshot] * 3))

    assert len(results) == 3
    assert len(set(results)) == 1
