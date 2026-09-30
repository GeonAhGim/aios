"""Adversarial -- AI-18 `ModelCard.model_hash` sha256-hex guard
(`src/foundation/ml/contracts/v1.py::_validate_sha256_hex`).

DEEPEN of the orphaned package marker (task-6704 qa-2 -> task-10095). The
happy-path/negative suite in `tests/foundation/unit/ml/test_contracts_v1.py`
covers one negative case (a non-hex string). This file plays the attacker:
someone trying to sneak a digest that merely *resembles* a lowercase sha256
hex string past the guard through a disguise, rather than an honest typo.

A model's `model_hash` is what `domain/registry_rules.py::
validate_new_registration` compares byte-for-byte to detect a silent weight
swap under the same (model_id, version) -- if the contract layer accepted a
malformed hash, that comparison's guarantee would be built on sand.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from src.foundation.ml.contracts import v1 as contracts_v1
from src.foundation.ml.contracts.v1 import ModelCard, TrainDataLineage

_NOW = datetime.now(timezone.utc)
_LINEAGE = TrainDataLineage(
    start=_NOW - timedelta(days=30), end=_NOW - timedelta(days=1), source_ref="s3://x"
)


def _card(model_hash: str) -> ModelCard:
    return ModelCard(
        model_id="m",
        version="1",
        model_hash=model_hash,
        train_data_lineage=_LINEAGE,
        trained_at=_NOW,
    )


# --- negative (>= 3): a disguised digest must still be rejected ---


def test_rejects_uppercase_hex_disguised_as_a_valid_digest() -> None:
    """Uppercase hex digits are the same 64-character length and the same
    alphabet family as a real sha256 hex digest -- an attacker (or a buggy
    upstream digest() call using `.hexdigest().upper()`) might expect this
    to pass. The contract requires *lowercase* hex specifically (matching
    `hashlib.sha256(...).hexdigest()`'s own output convention), so this
    must be rejected rather than silently normalized."""
    with pytest.raises(ValidationError):
        _card("A" * 64)


def test_rejects_hash_padded_with_whitespace() -> None:
    """A trailing space is invisible in most log/diff views, so a digest
    like `"aaaa...a "` could slip through casual review while still
    reading as "the right hash" to a human. `fullmatch` on the 64-char
    pattern must still reject the extra character."""
    with pytest.raises(ValidationError):
        _card("a" * 64 + " ")


def test_rejects_fullwidth_unicode_digits_mimicking_hex() -> None:
    """Fullwidth Unicode digits (U+FF10..) render as ordinary-looking
    digits in many fonts/terminals but are not in the ASCII `[0-9a-f]`
    class the guard checks -- a homoglyph attack against anyone
    eyeballing a diff. Must be rejected, not coerced."""
    with pytest.raises(ValidationError):
        _card("０" * 64)


# --- failure injection ---


def test_regex_engine_failure_propagates_uncaught() -> None:
    """`_validate_sha256_hex` has no try/except around the `fullmatch`
    call. If the compiled pattern itself misbehaves (e.g. a corrupted
    `re` extension, or a future refactor that swaps in a user-supplied
    pattern object), the resulting exception must propagate through
    pydantic's validation and out to the caller -- not be swallowed into
    a false "valid" result. Standard-105 fail-closed: an unexpected
    failure during validation must never resolve to acceptance."""

    class _BoomPattern:
        def fullmatch(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("regex engine exploded")

    original = contracts_v1._SHA256_HEX_RE
    contracts_v1._SHA256_HEX_RE = _BoomPattern()  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="regex engine exploded"):
            _card("a" * 64)
    finally:
        contracts_v1._SHA256_HEX_RE = original
