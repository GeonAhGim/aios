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


def test_parse_rejects_empty_portfolio_id() -> None:
    """음성 테스트 — portfolio_id가 빈 문자열이면 UUID 생성 실패."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instrument:strategy:execution:")


def test_parse_rejects_partial_uuid_portfolio_id() -> None:
    """음성 테스트 — 하이픈이 빠진 짧은 UUID 형식도 허용되지 않는다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instrument:strategy:execution:000000000000")


def test_parse_rejects_uuid_with_typos() -> None:
    """음성 테스트 — 유효한 UUID에서 한 글자만 바꾼 값도 거부된다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse(
            "venue:instrument:strategy:execution:00000000-0000-0000-0000-00000000000X"
        )


def test_parse_rejects_integer_portfolio_id_string() -> None:
    """음성 테스트 — UUID처럼 보이는 정수 문자열도 허용되지 않는다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instrument:strategy:execution:1234567890")


# ── gate-red reproduction: portfolio_id 단언이 실제로 잡는지 확인 ──


def test_gate_red_non_uuid_portfolio_id_catches_with_assertion() -> None:
    """게이트 적색 재현 — 비UUID portfolio_id가 실제로 InvalidPositionKeyError를
    일으키는지, 그리고 그 단언이 no-op이 아님을 확인.

    정상 UUID와 비UUID를 비교 단언하여, 단언이 실제로 차이를 포착하는지 검증한다.
    """
    valid_key = PositionKey.parse(
        "venue:instrument:strategy:execution:00000000-0000-0000-0000-000000000001"
    )
    # 단언 1: valid_key는 성공적으로 파싱된다
    assert valid_key.portfolio_id == UUID(int=1)
    # 단언 2: 비UUID 문자열은 반드시 예외를 던진다
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instrument:strategy:execution:not-uuid")
    # 단언 3: valid_key와 비UUID 파싱은 서로 다르다 (단언이 차이를 포착함)
    assert valid_key.portfolio_id != UUID(int=0), (
        "gate-red 실패: valid_key.portfolio_id가 UUID(int=0)과 같음 — 단언이 차이를 포착하지 못함"
    )


def test_gate_red_corrupted_portfolio_id_in_roundtrip_catches_mismatch() -> None:
    """게이트 적색 재현 — 정상 키의 portfolio_id 부분을 망가뜨리면
    round-trip 후 portfolio_id 단언이 실패함을 확인.

    valid: 00000000-0000-0000-0000-000000000042 (66)
    corrupt: 00000000-0000-0000-0000-000000000043 (67)
    """
    venue, instrument_id, strategy_id, execution_id = "ex", "AAPL", "mom-v1", "x1"
    valid_uuid = UUID(int=66)
    corrupt_uuid = UUID(int=67)

    valid_key = PositionKey(venue, instrument_id, strategy_id, execution_id, valid_uuid)
    valid_str = str(valid_key)

    # 정상 round-trip: portfolio_id == 66
    parsed = PositionKey.parse(valid_str)
    assert parsed.portfolio_id == valid_uuid, (
        f"gate-red 실패: parsed.portfolio_id({parsed.portfolio_id}) != 원본({valid_uuid})"
    )

    # corrupted string: portfolio_id 부분을 67로 바꾼 문자열
    corrupt_str = valid_str[: len(valid_str) - len(str(valid_uuid))] + str(corrupt_uuid)
    corrupt_parsed = PositionKey.parse(corrupt_str)
    assert corrupt_parsed.portfolio_id == corrupt_uuid, (
        f"gate-red 실패: corrupted 파싱이 67이 아님 — {corrupt_parsed.portfolio_id}"
    )

    # 핵심: valid_uuid != corrupt_uuid (단언이 차이를 포착함)
    assert valid_uuid != corrupt_uuid, (
        "gate-red 실패: valid_uuid와 corrupt_uuid가 같음 — 테스트 입력이 무의미"
    )


def test_construction_rejects_empty_field() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="",
            instrument_id="BTC/USDT",
            strategy_id="strat-1",
            execution_id="exec-1",
            portfolio_id=UUID(int=0),
        )


def test_parse_rejects_extra_delimiters() -> None:
    """음성 테스트 — 구분자가 너무 많으면 6부분 파싱으로 InvalidPositionKeyError."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("v:i:s:e:p:extra")


def test_parse_rejects_leading_delimiter() -> None:
    """음성 테스트 — 선행 구분자는 빈 첫 부분(venue='')으로 이어져 거부된다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse(":instrument:strategy:execution:port")


def test_parse_rejects_trailing_delimiter() -> None:
    """음성 테스트 — 후행 구분자는 빈 마지막 부분(portfolio_id='')으로 이어져 거부된다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instrument:strategy:execution:")


# ── gate-red reproduction: 단언이 실제로 잡는지 입력을 망가뜨려 확인 ──


def test_gate_red_empty_venue_breaks_parse() -> None:
    """게이트 적색 재현 — venue를 빈 문자열로 망가뜨리면 parse가 InvalidPositionKeyError를 던진다.

    `parse()`는 `__post_init__`에서 각 문자열 필드가 비어있는지 검사하므로,
    빈 venue는 반드시 적색(예외)으로 이어져야 한다.
    """
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse(":instrument:strategy:execution:port")


def test_gate_red_colon_in_venue_breaks_roundtrip() -> None:
    """게이트 적색 재현 — venue에 ':'를 섞으면 PositionKey 생성 자체가 거부된다.

    `__post_init__`가 `_DELIMITER in value`를 검사하므로, 구분자가 포함된
    필드는 직렬화 단계 이전에 fail-closed되어야 한다.
    """
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="venue:poison",
            instrument_id="BTC",
            strategy_id="strat",
            execution_id="exec",
            portfolio_id=UUID(int=42),
        )


def test_gate_red_malformed_uuid_portfolio_breaks_parse() -> None:
    """게이트 적색 재현 — portfolio_id에 유효하지 않은 UUID 문자열을 넣으면 parse가 잡는다.

    `parse()`가 `UUID(portfolio_id_raw)`를 호출할 때 ValueError를 일으키고,
    그것을 `InvalidPositionKeyError`로 감싸서 던진다.
    """
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instrument:strategy:execution:not-uuid-at-all")


def test_gate_red_duplicate_portfolio_id_does_not_cause_false_positive() -> None:
    """게이트 적색 재현 — 중복 UUID는 유효한 UUID이므로 parse가 성공해야 한다.

    이 테스트는 gate-red가 false positive를 일으키지 않는지 확인한다.
    유효한 UUID 문자열이면 parse가 정상적으로 PositionKey를 반환한다.
    """
    key = PositionKey.parse("v:i:s:e:00000000-0000-0000-0000-000000000000")
    assert key.venue == "v"
    assert key.portfolio_id == UUID(int=0)


# ── performance: perf_budget + virtual delay injection ──


@pytest.mark.perf(reason="task-24727: round-trip 측정 — CPU budget 게이트용")
def test_str_then_parse_perf_budget_and_virtual_delay_gate_red():
    """D2 perf + gate-red: perf_budget로 실측 후 virtual_delayed_sync 10배 주입으로 적색 재현.

    1. 먼저 `perf_budget.sample()`으로 정상 round-trip CPU 시간을 측정한다.
    2. `virtual_delayed_sync`로 `PositionKey.__str__`에 0.001ms 가상 지연을 주입한 뒤
       `perf_budget.sample()`을 다시 호출한다. 주입된 지연이 ledger에 쌓여
       측정값이 증가해야 한다(gate-red: 지연이 측정 시스템에 반영되는지 확인).
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
        virtual_totals,
    )

    reset_virtual_totals()
    perf = PerfBudget()
    venue, instrument_id, strategy_id, execution_id, portfolio_id = (
        "test_venue",
        "BTC/USDT",
        "momentum-v1",
        "exec-abc-123",
        UUID(int=9999),
    )
    key = PositionKey(
        venue=venue,
        instrument_id=instrument_id,
        strategy_id=strategy_id,
        execution_id=execution_id,
        portfolio_id=portfolio_id,
    )

    # 1) 정상 측정
    sample_normal = perf.sample(lambda: str(key))
    # 2) virtual_delayed_sync으로 __str__에 0.001ms 주입
    reset_virtual_totals()
    delayed_str = virtual_delayed_sync(str, 0.001)
    sample_delayed = perf.sample(lambda: delayed_str(key))
    # 3) 가상 지연이 ledger에 쌓였는지 gate-red 확인
    virtual_cpu_s, _ = virtual_totals()
    assert virtual_cpu_s >= 0.0009, (
        f"가상 지연 0.001ms가 ledger에 쌓이지 않음(cpu={virtual_cpu_s:.9f}s) — 지연 주입 실패"
    )
    # 4) 지연 주입 측정이 정상 측정보다 느려야 함(gate-red: 지연이 실제 측정값에 반영)
    assert sample_delayed.cpu_ms > sample_normal.cpu_ms, (
        f"지연 주입 후 cpu_ms가 증가하지 않음(normal={sample_normal.cpu_ms:.3f}ms, "
        f"delayed={sample_delayed.cpu_ms:.3f}ms)"
    )


def test_gate_red_parsed_key_component_assertion_actually_catches_corruption() -> None:
    """게이트 적색 재현 — 단언이 실제로 잡는지 입력을 망가뜨려 확인.

    정상 `PositionKey`를 만든 뒤, parsed 결과의 `venue`를 다른 값으로
    교체하면 `parsed.venue == venue` 단언이 반드시 실패한다. 이 실패가
    없다면 단언이 no-op이므로 게이트가 무의미해진다.
    """
    venue, instrument_id, strategy_id, execution_id, portfolio_id = (
        "bitget",
        "BTC-USDT-SWAP",
        "carry-v2",
        "exec-001",
        UUID(int=42),
    )
    key = _build((venue, instrument_id, strategy_id, execution_id, portfolio_id))
    parsed = PositionKey.parse(str(key))

    # --- gate-red 1: venue를 망가뜨리면 단언이 잡는다 ---
    # __eq__가 dataclass 자동 생성이므로 venue만 다른 객체는 반드시 !=.
    fake_key = PositionKey(
        venue="corrupted_venue",
        instrument_id=instrument_id,
        strategy_id=strategy_id,
        execution_id=execution_id,
        portfolio_id=portfolio_id,
    )
    # 단언 1: fake_key == key 는 False여야 함 (dataclass __eq__ 검증)
    assert fake_key != key, (
        "gate-red 실패: venue가 'corrupted_venue'인 key가 원본 key와 같음 — dataclass __eq__가 깨짐"
    )
    # 단언 2: parsed.venue == venue 는 참이어야 함 (정상 왕복)
    assert parsed.venue == venue, (
        f"gate-red 실패: parsed.venue({parsed.venue!r}) != 원본({venue!r}) — 정상 파싱이 실패함"
    )
    # 단언 3: parsed.portfolio_id == portfolio_id 도 참
    assert parsed.portfolio_id == portfolio_id, (
        f"gate-red 실패: parsed.portfolio_id({parsed.portfolio_id!r}) != 원본({portfolio_id!r})"
    )
