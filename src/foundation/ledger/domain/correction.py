"""FA-11 — ledger/domain/correction.py: reversal + re-posting for erroneous entries.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-11
(선행 FA-10=task-2051).

FA-A2(상태성 테이블 UPDATE/DELETE 금지)는 posted journal entry에도 적용된다 —
`ledger_journal_entry`/`ledger_posting_line`는 이미 append-only 해시체인(LC-3)
이라 물리적으로 UPDATE가 불가하다. 그래서 잘못 기표된 분개를 고치는 유일한
방법은 두 개의 새 분개를 posting하는 것이다: "역분개"(원본을 상쇄하는 분개,
모든 행의 side를 뒤집는다) + "재기표"(옳은 값으로 새로 기표하는 분개). 이
모듈은 그 두 벌의 `PostingLine`을 만드는 순수 규칙만 담는다 — 실제
`post_entry`(LC-9)를 통한 append는 application 계층(이 리프 범위 밖)의 몫이다.

균형·통화 검증은 LC-3 `balance_rules.check_balanced`를 그대로 재사용한다(재구현
금지, task-2058 decision). 재기표가 원본과 다이제스트까지 완전히 같으면
"정정"이 아니므로 거부한다 — 다이제스트 계산도 LC-3 `hash_chain.lines_digest`를
재사용한다. 순수 함수만 — I/O·시계 직접 호출 금지.
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
    """정정 대상 분개(또는 재기표 분개)가 빈 행 목록이다."""


class BlankReasonError(ValueError):
    """정정 사유가 비어 있다 — 정정은 항상 감사 추적이 가능해야 한다(§8)."""


class NoOpCorrectionError(ValueError):
    """재기표 행이 원본과 다이제스트가 같다 — 아무것도 바꾸지 않는 정정은
    허용하지 않는다(호출자 버그이거나 잘못된 사유로 만든 정정 요청)."""

    def __init__(self, original_entry_id: UUID) -> None:
        super().__init__(
            f"{original_entry_id}: 재기표 행이 원본과 동일합니다(digest 일치) — 정정이 아닙니다."
        )


def reversal_lines(original_lines: Sequence[PostingLine]) -> list[PostingLine]:
    """원본 분개 행은 그대로 두고(UPDATE 금지) side만 뒤집은 상쇄 분개를 만든다.
    `line_no`·`account_code`·`amount`·`currency`는 그대로 보존한다 — 원본이 이미
    균형 상태로 posting됐다면(LC-9가 보장) 역분개도 자동으로 균형이지만, 이
    함수는 그 사실에 기대지 않고 fail-closed로 다시 확인한다(LC-3 재사용)."""
    if not original_lines:
        raise EmptyEntryError("정정할 원본 분개 행이 비어 있습니다.")
    flipped = [
        line.model_copy(update={"side": _FLIPPED_SIDE[line.side]}) for line in original_lines
    ]
    balance_rules.check_balanced(flipped)
    return flipped


@dataclass(frozen=True, slots=True)
class LedgerCorrection:
    """정정 하나 = 역분개 + 재기표. 둘 다 새 분개다(FA-A2, 원본에는 UPDATE가
    없다). `original_entry_id`는 어느 분개를 상쇄하는지 감사 추적용으로
    남긴다."""

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
    """원본 분개(`original_lines`)를 역분개로 상쇄하고 `corrected_lines`로
    재기표할 준비를 한다. 두 벌 다 `balance_rules.check_balanced`(LC-3)를
    통과해야 하며, 재기표가 원본과 완전히 같으면(digest 동일) 거부한다."""
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
