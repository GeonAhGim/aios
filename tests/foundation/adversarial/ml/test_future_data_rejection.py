"""Adversarial -- future data model (spec §8 verbatim: "미래 데이터 모델").

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §8 ("adversarial:
... a future-data model"), §4 A-3, §9 AI-18 DoD ("future data rejection
(A-3)"). Task-2653.

Unlike `tests/foundation/unit/ml/test_point_in_time.py`'s boundary-value
negative tests, this file plays the attacker: a caller who tries to slip a
model that saw future data past `check_point_in_time` through a disguise
rather than an honest mistake.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage
from src.foundation.ml.domain.point_in_time import FutureDataLeakageError, check_point_in_time

_UTC_NOW = datetime.now(timezone.utc)


def _card(lineage_end: datetime) -> ModelCard:
    return ModelCard(
        model_id="momentum-lgbm",
        version="1",
        model_hash="a" * 64,
        train_data_lineage=TrainDataLineage(
            start=lineage_end - timedelta(days=30),
            end=lineage_end,
            source_ref="parquet://features/v1",
        ),
        trained_at=lineage_end,
    )


def test_rejects_future_data_disguised_behind_a_non_utc_offset() -> None:
    """The attacker reports `train_data_lineage.end` in a +09:00 offset,
    hoping a naive numeric/string comparison (rather than proper aware-
    datetime comparison by absolute instant) would misjudge it as "before"
    the backtest start. Python's aware-datetime comparison normalizes by
    instant, so this must still reject when the true instant is >= start."""
    backtest_start = _UTC_NOW
    kst = timezone(timedelta(hours=9))
    # This instant is 1 second after backtest_start in absolute time, even
    # though its local KST wall-clock reads earlier in the day than a naive
    # UTC-string comparison of just the clock digits might suggest.
    lineage_end_kst = (backtest_start + timedelta(seconds=1)).astimezone(kst)
    card = _card(lineage_end_kst)

    with pytest.raises(FutureDataLeakageError):
        check_point_in_time(card, backtest_start=backtest_start)


def test_accepts_genuinely_past_data_reported_in_a_non_utc_offset() -> None:
    """Control for the test above: the same non-UTC offset trick must NOT
    cause a false rejection when the underlying instant is genuinely
    before the backtest start -- proves the guard compares instants, not
    offsets or formatted strings."""
    backtest_start = _UTC_NOW
    kst = timezone(timedelta(hours=9))
    lineage_end_kst = (backtest_start - timedelta(days=1)).astimezone(kst)
    card = _card(lineage_end_kst)

    check_point_in_time(card, backtest_start=backtest_start)  # no raise


def test_rejects_lineage_end_matching_backtest_start_to_the_microsecond() -> None:
    """The attacker trains right up to (but not literally past) the
    backtest's first instant, betting that "not past" reads as "safe". A-3
    requires strictly-before, so this must still reject."""
    backtest_start = _UTC_NOW
    card = _card(backtest_start)

    with pytest.raises(FutureDataLeakageError):
        check_point_in_time(card, backtest_start=backtest_start)


def test_rejects_naive_backtest_start_instead_of_silently_comparing() -> None:
    """A caller passes a naive `backtest_start` (e.g. a local-clock mistake
    upstream, or an attacker hoping a naive/aware comparison quietly
    succeeds via an implicit assumption). The guard must fail closed with a
    plain `ValueError` about tz-awareness -- and this must NOT be
    misreported as `FutureDataLeakageError`, since that would mislead a
    caller into thinking the model itself was rejected for leakage rather
    than the call being malformed."""
    card = _card(_UTC_NOW - timedelta(days=1))
    naive_start = datetime(2026, 1, 1)  # no tzinfo

    with pytest.raises(ValueError, match="timezone-aware") as exc_info:
        check_point_in_time(card, backtest_start=naive_start)

    assert not isinstance(exc_info.value, FutureDataLeakageError)


def test_propagates_error_construction_failure_instead_of_masking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure injection: simulate `FutureDataLeakageError` itself being
    broken (e.g. a bad deploy, or a corrupted dependency that raises while
    building the rejection error). The guard must stay fail-closed -- it
    must propagate whatever exception surfaces rather than swallowing it
    and letting the call through as if it had been approved."""
    backtest_start = _UTC_NOW
    card = _card(backtest_start)  # tie -> would normally raise FutureDataLeakageError

    def _broken_init(self: FutureDataLeakageError, *args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure: error construction is broken")

    monkeypatch.setattr(FutureDataLeakageError, "__init__", _broken_init)

    with pytest.raises(RuntimeError, match="injected failure"):
        check_point_in_time(card, backtest_start=backtest_start)
