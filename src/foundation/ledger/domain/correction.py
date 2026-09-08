"""FA-11 — ledger/domain/correction.py: reversal + re-posting for erroneous entries.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-11
(precedes FA-10=task-2051).

FA-A2 (no UPDATE/DELETE on stateful tables) applies to posted journal
entries too — `ledger_journal_entry`/`ledger_posting_line` are already an
append-only hash chain (LC-3), so UPDATE is physically impossible. So the
only way to fix a misposted entry is to post two new entries: a "reversal"
(an entry that offsets the original, flipping every line's side) plus a
"repost" (a new entry posted with the correct values). This module only
holds the pure rules for building those two sets of `PostingLine`s — the
actual append via `post_entry` (LC-9) is the application layer's job (out
of this leaf's scope).

Balance/currency validation reuses LC-3 `balance_rules.check_balanced`
as-is (no reimplementation, task-2058 decision). If the repost is fully
identical to the original down to the digest, it's rejected as not being a
"correction" — the digest computation also reuses LC-3
`hash_chain.lines_digest`. Pure functions only — no I/O, no direct clock
calls.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from src.foundation.ledger.contracts.v1 import PostingLine, Side
from src.foundation.ledger.domain import balance_rules
from src.foundation.ledger.domain.hash_chain import lines_digest

_FLIPPED_SIDE = {Side.DEBIT: Side.CREDIT, Side.CREDIT: Side.DEBIT}


class EmptyEntryError(ValueError):
    """The entry being corrected (or the repost entry) is an empty list of lines."""


class BlankReasonError(ValueError):
    """The correction reason is blank — a correction must always be auditable (§8)."""


class NoOpCorrectionError(ValueError):
    """The repost lines have the same digest as the original — a correction
    that changes nothing is not allowed (either a caller bug, or a
    correction request built on a bad reason)."""

    def __init__(self, original_entry_id: UUID) -> None:
        super().__init__(
            f"{original_entry_id}: 재기표 행이 원본과 동일합니다(digest 일치) — 정정이 아닙니다."
        )


def reversal_lines(original_lines: Sequence[PostingLine]) -> list[PostingLine]:
    """Leaves the original entry's lines untouched (no UPDATE) and builds an
    offsetting entry with only `side` flipped. `line_no`/`account_code`/
    `amount`/`currency` are all preserved as-is — if the original was
    already posted balanced (guaranteed by LC-9), the reversal is
    automatically balanced too, but this function doesn't rely on that fact
    and re-verifies it fail-closed (reusing LC-3)."""
    if not original_lines:
        raise EmptyEntryError("정정할 원본 분개 행이 비어 있습니다.")
    flipped = [
        line.model_copy(update={"side": _FLIPPED_SIDE[line.side]}) for line in original_lines
    ]
    balance_rules.check_balanced(flipped)
    return flipped


@dataclass(frozen=True, slots=True)
class LedgerCorrection:
    """One correction = a reversal + a repost. Both are new entries (FA-A2,
    the original never gets an UPDATE). `original_entry_id` is kept for
    audit-trail purposes, recording which entry is being offset."""

    original_entry_id: UUID
    reason: str
    reversal: tuple[PostingLine, ...]
    repost: tuple[PostingLine, ...]


def build_correction(
    *,
    original_entry_id: UUID,
    original_lines: Sequence[PostingLine],
    corrected_lines: Sequence[PostingLine],
    reason: str,
) -> LedgerCorrection:
    """Offsets the original entry (`original_lines`) with a reversal and
    prepares to repost with `corrected_lines`. Both sets must pass
    `balance_rules.check_balanced` (LC-3), and the repost is rejected if it
    is fully identical to the original (same digest)."""
    if not reason.strip():
        raise BlankReasonError("정정 사유(reason)는 비워둘 수 없습니다 — 감사 추적 필수.")
    if not corrected_lines:
        raise EmptyEntryError("재기표할 분개 행이 비어 있습니다.")

    reversal = reversal_lines(original_lines)
    balance_rules.check_balanced(corrected_lines)

    if lines_digest(original_lines) == lines_digest(corrected_lines):
        raise NoOpCorrectionError(original_entry_id)

    return LedgerCorrection(
        original_entry_id=original_entry_id,
        reason=reason,
        reversal=tuple(reversal),
        repost=tuple(corrected_lines),
    )
