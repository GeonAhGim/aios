"""H-8 property 테스트 — `PositionKey` 왕복(round-trip) 불변식.

Spec: docs/design/ADR-2026-09-09-B(H-8) +
docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2,
`src/foundation/positions/domain/position_key.py`(FA-0d, 5부분
`venue:instrument_id:strategy_id:execution_id:portfolio_id`).

왕복 불변식: 어떤 유효한 필드 조합이든 `PositionKey.parse(str(key)) == key`
여야 한다(직렬화·역직렬화가 항등 함수의 역이다). hypothesis 예제 DB는
`.hypothesis/`에만 생기고 커밋하지 않는다(`.gitignore`).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.foundation.positions.domain.position_key import (
    InvalidPositionKeyError,
    PositionKey,
)

# ':'(구분자)만 제외한 비어있지 않은 문자열 — 다른 특수문자·유니코드는 허용.
_FIELD_TEXT = st.text(
    alphabet=st.characters(blacklist_characters=":", blacklist_categories=("Cs",)),
    min_size=1,
    max_size=32,
).filter(lambda s: s.strip("\x00") == s)  # NUL만 배제, 그 외 공백류는 허용.

_UUIDS = st.uuids()

_KEY_FIELDS = st.tuples(_FIELD_TEXT, _FIELD_TEXT, _FIELD_TEXT, _FIELD_TEXT, _UUIDS)


def _build(fields: tuple[str, str, str, str, UUID]) -> PositionKey:
    venue, instrument_id, strategy_id, execution_id, portfolio_id = fields
    return PositionKey(
        venue=venue,
        instrument_id=instrument_id,
        strategy_id=strategy_id,
        execution_id=execution_id,
        portfolio_id=portfolio_id,
    )


@given(_KEY_FIELDS)
def test_str_then_parse_round_trips_to_an_equal_key(
    fields: tuple[str, str, str, str, UUID],
) -> None:
    key = _build(fields)
    assert PositionKey.parse(str(key)) == key


@given(_KEY_FIELDS)
def test_parsed_key_preserves_every_component(fields: tuple[str, str, str, str, UUID]) -> None:
    venue, instrument_id, strategy_id, execution_id, portfolio_id = fields
    key = _build(fields)
    parsed = PositionKey.parse(str(key))
    assert parsed.venue == venue
    assert parsed.instrument_id == instrument_id
    assert parsed.strategy_id == strategy_id
    assert parsed.execution_id == execution_id
    assert parsed.portfolio_id == portfolio_id


@given(_KEY_FIELDS, st.sampled_from(("venue", "instrument_id", "strategy_id", "execution_id")))
def test_delimiter_inside_a_field_is_rejected_instead_of_silently_corrupting_the_key(
    fields: tuple[str, str, str, str, UUID], poisoned_field: str
) -> None:
    """음성 테스트 — 구분자(':')가 필드 값 안에 들어오면 5부분 파싱이
    깨져 다른 필드로 값이 새는 왕복 불일치가 생긴다. 생성 시점에
    `InvalidPositionKeyError`로 거부해야 한다(fail-closed)."""
    values = dict(
        zip(("venue", "instrument_id", "strategy_id", "execution_id"), fields[:4], strict=True)
    )
    values[poisoned_field] = values[poisoned_field] + ":poison"

    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue=values["venue"],
            instrument_id=values["instrument_id"],
            strategy_id=values["strategy_id"],
            execution_id=values["execution_id"],
            portfolio_id=fields[4],
        )


def test_parse_rejects_wrong_part_count() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instrument:strategy")


def test_parse_rejects_non_uuid_portfolio_id() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instrument:strategy:execution:not-a-uuid")


def test_construction_rejects_empty_field() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="",
            instrument_id="BTC/USDT",
            strategy_id="strat-1",
            execution_id="exec-1",
            portfolio_id=UUID(int=0),
        )
