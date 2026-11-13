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
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

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


# ── gate_red: 단언이 실제로 잡는지 확인 (입력을 망가뜨려 적색 재현) ──


@given(st.text(min_size=1))
def test_gate_red_parse_extra_delimiter_causes_part_count_mismatch(
    extra: str,
) -> None:
    """gate_red 재현 — 6부분 문자열을 parse()에 넘기면 part_count 단언이
    반드시 잡는다. 만약 단언이 제거되면 InvalidPositionKeyError가 발생하지
    않고 잘못된 PositionKey가 돌아온다."""
    # 5부분에 extra를 붙여 6부분 이상 만든다.
    raw = f"v:{extra}:instr:strat:exec:{UUID(int=42)}"
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse(raw)


def test_gate_red_parse_empty_field_in_string() -> None:
    """gate_red 재현 — 빈 필드(`::`)가 문자열에 있으면 parse()가
    InvalidPositionKeyError를 던진다. PositionKey.__post_init__의
    empty-check가 .parse() 복원 경로에서도 유효함을 확인한다.

    단언이 제거되면 빈 문자열을 가진 PositionKey가 생성되어
    downstream에서 silent corruption이 일어난다."""
    raw = "venue::strategy:execution:00000000-0000-0000-0000-000000000042"
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse(raw)


def test_gate_red_construction_non_uuid_portfolio() -> None:
    """gate_red 재현 — portfolio_id에 UUID가 아닌 값을 넣으면
    __post_init__이 isinstance(portfolio_id, UUID) 단언으로
    InvalidPositionKeyError를 던진다.

    단언 제거 시 문자열 portfolio_id가 PositionKey에 유입되어
    DB 조회 시 키 불일치를 유발한다."""
    with pytest.raises(InvalidPositionKeyError):
        # Dynamically pass a non-UUID to bypass static type checking
        PositionKey(
            venue="venue",
            instrument_id="BTC/USDT",
            strategy_id="strat-1",
            execution_id="exec-1",
            **{"portfolio_id": "not-a-uuid"},
        )


@pytest.mark.perf
def test_perf_parse_measurement_sensitivity_to_virtual_delay() -> None:
    """성능 게이트 적색 재현 — PerfBudget이 가상 지연을 정확히 감지하는지
    확인한다. 지연 0ms와 지연 50ms를 비교해 측정 시스템이 지연을 포착함을
    증명한다.

    virtual_delayed_sync로 50ms(10x 기준) 지연을 주입한 후
    PerfBudget.best_of로 측정한다. 지연 없는 baseline과 비교해
    측정값이 ~50ms 증가함을 단언한다.

    실측 기준: 단일 parse() 호출은 보통 <0.05ms CPU (local SSD,
    Python 3.13). 50ms 가상 지연은 실측의 1000배 이상."""
    reset_virtual_totals()

    original_parse = PositionKey.parse
    parse_str = "venue:instrument:strategy:execution:00000000-0000-0000-0000-000000000042"

    budget = PerfBudget()

    # baseline: 지연 없음
    baseline = budget.best_of(lambda: original_parse(parse_str), n=5)

    # 10x 가상 지연(50ms) 주입
    reset_virtual_totals()
    delayed_parse = virtual_delayed_sync(original_parse, 0.05)
    with_delay = budget.best_of(lambda: delayed_parse(parse_str), n=5)

    # 측정값이 ~50ms 증가했어야 함 (실측 오차 ±10ms 허용)
    delta_ms = with_delay.cpu_ms - baseline.cpu_ms
    assert delta_ms >= 40.0, (
        f"PerfBudget failed to detect 50ms virtual delay: delta={delta_ms:.3f}ms (expected>=40.0ms)"
    )
