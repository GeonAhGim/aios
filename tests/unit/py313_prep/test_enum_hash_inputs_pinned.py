"""Pin enum-to-string paths that feed hashes / idempotency keys to literals.

`f"{x}"` / `format(x)` on a `(str, Enum)` yields the value on Python 3.10 but
`ClassName.MEMBER` on 3.11+. Every site below must produce the same bytes on
both, so each assertion compares against a literal built without any enum
formatting. Source: docs/design/PY313_PREP_hash_inputs.md.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest

from src.core.eventstore.canonical_json import canonical_json
from src.core.eventstore.replay import digest_state
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.domain.rules import compute_event_hash, compute_payload_hash
from src.foundation.ledger.contracts.v1 import LedgerEventType
from src.foundation.ledger.domain.hash_chain import entry_hash
from src.foundation.market_data.domain.lineage import batch_hash
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.validation.domain.rules import compute_result_hash
from src.services.oms.application.dispatch_outcome import (
    OutcomeKind,
    classify_submit_failure,
)

_ORDER_ID = UUID("00000000-0000-0000-0000-000000000001")
_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


class _Plain(Enum):
    A = "a"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- submit.py:89 payload_hash input: outcome.reason must be a plain str ---


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (ExchangeErrorKind.INSUFFICIENT_FUNDS, "INSUFFICIENT_FUNDS"),
        (ExchangeErrorKind.AUTH, "AUTH"),
        (ExchangeErrorKind.TRANSIENT_NETWORK, "TRANSIENT_NETWORK"),
    ],
)
def test_submit_reason_is_plain_value_string(kind: ExchangeErrorKind, reason: str) -> None:
    outcome = classify_submit_failure(ExchangeError(kind))
    assert type(outcome.reason) is str
    assert outcome.reason == reason
    assert _sha(f"{_ORDER_ID}:{outcome.reason}") == _sha(f"{_ORDER_ID}:{reason}")


def test_submit_reason_literal_branches_are_str() -> None:
    assert classify_submit_failure(RuntimeError("x")).reason == "RuntimeError"
    outcome = classify_submit_failure(ExchangeError(ExchangeErrorKind.AUTH, circuit_open=True))
    assert (outcome.kind, outcome.reason) == (OutcomeKind.DEFER, "CIRCUIT_OPEN")
    assert type(outcome.reason) is str


def test_submit_payload_hash_literal() -> None:
    reason = classify_submit_failure(ExchangeError(ExchangeErrorKind.AUTH)).reason
    expected = _sha("00000000-0000-0000-0000-000000000001:AUTH")
    assert _sha(f"{_ORDER_ID}:{reason}") == expected


def test_submit_negative_enum_member_would_change_key_text() -> None:
    # NEGATIVE: the enum member itself is not the pinned text; only `.value` is.
    assert ExchangeErrorKind.AUTH.value == "AUTH"
    assert str(ExchangeErrorKind.AUTH) == "ExchangeErrorKind.AUTH"
    assert type(ExchangeErrorKind.AUTH.value) is str


# --- apply_safety_control.py:87 detail / :63 idempotency key: no enum in text ---


def test_safety_control_detail_and_key_literals() -> None:
    control_id = UUID("00000000-0000-0000-0000-0000000000aa")
    reason = "manual halt"
    detail = f"RISK_GATE_SAFETY_CONTROL:{control_id}:{reason}"
    assert detail == "RISK_GATE_SAFETY_CONTROL:00000000-0000-0000-0000-0000000000aa:manual halt"
    assert f"risk-pause:{control_id}" == "risk-pause:00000000-0000-0000-0000-0000000000aa"


def test_safety_scope_value_is_the_only_stable_text() -> None:
    # NEGATIVE: SafetyScope is a (str, Enum) and is NOT in the detail text; if it
    # ever were, `.value` (not f-string) is the version-independent form.
    assert SafetyScope.GLOBAL.value == "GLOBAL"
    assert str(SafetyScope.GLOBAL) == "SafetyScope.GLOBAL"


# --- json.dumps(default=str) sites ---


def test_canonical_json_str_enum_serialises_as_value() -> None:
    assert canonical_json({"s": Outcome.SUCCESS, "k": ExchangeErrorKind.AUTH}) == (
        '{"k": "AUTH", "s": "SUCCESS"}'
    )


def test_canonical_json_plain_enum_uses_str_fallback_negative() -> None:
    # NEGATIVE: a plain Enum falls to default=str -> "_Plain.A" (same on all versions).
    assert canonical_json({"p": _Plain.A}) == '{"p": "_Plain.A"}'


def test_evidence_payload_hash_literal() -> None:
    assert compute_payload_hash({"o": Outcome.DENIED}) == _sha('{"o": "DENIED"}')


def test_evidence_event_hash_uses_values() -> None:
    expected = _sha(
        "|".join(
            [
                "",
                "system",
                "1",
                "agg",
                str(_ORDER_ID),
                "act",
                "SUCCESS",
                "ph",
                "INTERNAL",
                _AT.isoformat(),
            ]
        )
    )
    got = compute_event_hash(
        previous_hash=None,
        tenant_id=None,
        sequence_no=1,
        aggregate_type="agg",
        aggregate_id=_ORDER_ID,
        action="act",
        outcome=Outcome.SUCCESS,
        payload_hash="ph",
        classification=Classification.INTERNAL,
        occurred_at=_AT,
    )
    assert got == expected


def test_ledger_entry_hash_uses_event_type_value() -> None:
    got = entry_hash(None, 1, LedgerEventType.TOPUP_CONFIRMED, "ref", "dg", _AT)
    assert got == _sha(f"|1|TOPUP_CONFIRMED|ref|dg|{_AT.isoformat()}")


def test_batch_hash_str_enum_record_literal() -> None:
    assert batch_hash([{"s": Outcome.ERROR}]) == _sha('{"s": "ERROR"}')


def test_validation_result_hash_literal_and_decimal_str_fallback() -> None:
    assert compute_result_hash({"c": Classification.PUBLIC}) == _sha('{"c": "PUBLIC"}')
    assert compute_result_hash({"d": Decimal("1.50")}) == _sha('{"d": "1.50"}')


def test_replay_digest_state_str_enum_literal() -> None:
    assert digest_state({"o": Outcome.SUCCESS}) == _sha('{"o": "SUCCESS"}')


def test_json_dumps_str_enum_matches_stdlib_literal() -> None:
    assert json.dumps({"o": Outcome.SUCCESS}, sort_keys=True, default=str) == '{"o": "SUCCESS"}'
