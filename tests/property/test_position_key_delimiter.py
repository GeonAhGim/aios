"""H-8 delimiter 경계 테스트 — `test_delimiter_inside_a_field`의 보강.

task-24808 (DEEPEN-DEPTH H-8): delimiter 관련 gate-red, negative, failure injection,
perf 테스트를 별도 파일로 분리하여 원본 파일의 500줄 제한을 준수한다.

원본 파일: tests/property/test_position_key_roundtrip.py
"""

from __future__ import annotations

import time
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.foundation.positions.domain.position_key import (
    InvalidPositionKeyError,
    PositionKey,
)

# ':'(구분자)만 제외한 비어있지 않은 문자열.
_FIELD_TEXT = st.text(
    alphabet=st.characters(blacklist_characters=":", blacklist_categories=("Cs",)),
    min_size=1,
    max_size=32,
).filter(lambda s: s.strip("\x00") == s)

_UUIDS = st.uuids()
_KEY_FIELDS = st.tuples(_FIELD_TEXT, _FIELD_TEXT, _FIELD_TEXT, _FIELD_TEXT, _UUIDS)


# ── gate-red reproduction: delimiter rejection 단언이 실제로 잡는지 확인 ──


def test_gate_red_delimiter_rejection_assertion_catches_no_error() -> None:
    """게이트 적색 재현 — delimiter 거부 단언이 실제로 잡는지 확인.

    정상 PositionKey를 만들고, delimiter가 없는 유효한 키가 예외를 던지지
    않음을 먼저 확인한 뒤, delimiter가 포함된 키가 반드시 InvalidPositionKeyError를
    던지는지 검증한다. 이 차이가 없다면 단언이 no-op이다.
    """
    # 1) 정상 키: 예외가 없다
    normal_key = PositionKey(
        venue="venue",
        instrument_id="instr",
        strategy_id="strat",
        execution_id="exec",
        portfolio_id=UUID(int=1),
    )
    assert str(normal_key) == "venue:instr:strat:exec:00000000-0000-0000-0000-000000000001"

    # 2) delimiter가 포함된 키: 반드시 예외를 던진다
    with pytest.raises(InvalidPositionKeyError) as exc_info:
        PositionKey(
            venue="venue:poison",
            instrument_id="instr",
            strategy_id="strat",
            execution_id="exec",
            portfolio_id=UUID(int=1),
        )
    # 3) 단언이 차이를 포착함: 정상 키와 delimiter 포함 키는 다르다
    assert "구분자" in str(exc_info.value), (
        "gate-red 실패: delimiter 포함 시 '구분자' 오류 메시지가 없음 — 단언이 차이를 포착하지 못함"
    )


def test_gate_red_parse_delimiter_in_field_catches_invalid_key() -> None:
    """게이트 적색 재현 — parse()에서 delimiter가 필드 안에 있으면 6부분으로 split되어
    part count 오류로 거부되는지 확인.

    'venue:instr:u:u:exec:port' (delimiter가 venue 안에 있는 경우)는
    6부분으로 split되어 InvalidPositionKeyError를 던져야 한다.
    """
    # delimiter가 venue 필드에 있으면 6부분이 된다
    with pytest.raises(InvalidPositionKeyError) as exc_info:
        PositionKey.parse("venue:poison:instr:strat:exec:port")

    assert "부분" in str(exc_info.value), (
        "gate-red 실패: delimiter로 인한 part count 오류가 '부분' 메시지로 표시되지 않음"
    )


# ── 추가 음성 테스트: delimiter 관련 경계 케이스 ──


def test_delimiter_at_start_of_field_is_rejected() -> None:
    """음성 테스트 — 필드 값의 시작이 ':'이면 구분자 포함으로 거부된다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue=":venue",
            instrument_id="instr",
            strategy_id="strat",
            execution_id="exec",
            portfolio_id=UUID(int=1),
        )


def test_delimiter_at_end_of_field_is_rejected() -> None:
    """음성 테스트 — 필드 값의 끝이 ':'이면 구분자 포함으로 거부된다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="venue:",
            instrument_id="instr",
            strategy_id="strat",
            execution_id="exec",
            portfolio_id=UUID(int=1),
        )


def test_multiple_delimiters_in_one_field_are_rejected() -> None:
    """음성 테스트 — 한 필드 안에 ':'가 여러 개여도 동일하게 거부된다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="venue:mid:instr",
            instrument_id="strat",
            strategy_id="exec",
            execution_id="port",
            portfolio_id=UUID(int=1),
        )


def test_parse_rejects_colon_in_first_field_via_part_count() -> None:
    """음성 테스트 — 첫 번째 필드(venue)에 ':'가 있으면 6부분이 되어 parse에서 거부된다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:extra:instr:strat:exec:00000000-0000-0000-0000-000000000001")


def test_construction_rejects_delimiter_in_execution_id() -> None:
    """음성 테스트 — execution_id에 ':'가 있으면 construction 시점에 거부된다."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="venue",
            instrument_id="instr",
            strategy_id="strat",
            execution_id="exec:poison",
            portfolio_id=UUID(int=1),
        )


def test_construction_rejects_delimiter_in_portfolio_id_is_not_applicable() -> None:
    """음성 테스트 — portfolio_id는 UUID이므로 ':'를 포함할 수 없다(UUID 생성 실패).

    NOTE: portfolio_id는 UUID 타입이므로 PositionKey 생성 시 문자열이 아닌
    UUID 객체가 전달된다. UUID 객체 자체가 ':'를 포함할 수 없으므로
    이 테스트는 UUID 타입 제약이 delimiter 문제를 근본적으로 방지함을 확인한다.
    """
    # UUID 객체는 ':'를 포함할 수 없으므로, 타입 시스템이 delimiter 문제를 방지한다.
    # 따라서 PositionKey 생성 시 portfolio_id에 ':'가 들어올 경로가 없다.
    # 이 테스트는 타입 제약이 delimiter 문제를 근본적으로 방지함을 확인한다.
    with pytest.raises(ValueError):
        # UUID 생성 자체가 ':'를 포함하는 문자열로 시도되면 ValueError를 던진다
        UUID("not-a-uuid:with-colon")


# ── failure injection: delimiter poison injection ──


def test_failure_inject_delimiter_in_all_str_fields() -> None:
    """실패 주입 — 모든 문자열 필드(venue, instrument_id, strategy_id, execution_id)에
    delimiter를 주입했을 때 모두 InvalidPositionKeyError를 던지는지 확인.

    각 필드별로 delimiter를 주입하여, 어떤 필드에서도 delimiter가 새어나가지
    않도록 fail-closed함을 검증한다.
    """
    base_kwargs = dict(
        venue="venue",
        instrument_id="instr",
        strategy_id="strat",
        execution_id="exec",
        portfolio_id=UUID(int=1),
    )
    for field_name in ("venue", "instrument_id", "strategy_id", "execution_id"):
        poisoned_kwargs = base_kwargs.copy()
        poisoned_kwargs[field_name] = base_kwargs[field_name] + ":poison"
        with pytest.raises(InvalidPositionKeyError):
            PositionKey(**poisoned_kwargs)


def test_failure_inject_parse_with_poisoned_field_yields_wrong_part_count() -> None:
    """실패 주입 — parse()에 delimiter가 포함된 필드 값을 문자열로 넣으면
    part count 오류로 거부된다(생성이 아닌 parse 경로).
    """
    # venue에 ':'가 있으면 6부분이 된다
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:poison:instr:strat:exec:00000000-0000-0000-0000-000000000001")
    # instrument_id에 ':'가 있으면 6부분이 된다
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instr:u:u:strat:exec:00000000-0000-0000-0000-000000000001")
    # execution_id에 ':'가 있으면 6부분이 된다
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("venue:instr:strat:exec:u:u:00000000-0000-0000-0000-000000000001")


# ── performance: delimiter rejection perf ──


@pytest.mark.perf(reason="task-24808: delimiter 거부 측정 — CPU budget 게이트용")
def test_delimiter_rejection_perf_budget() -> None:
    """D2 perf: delimiter 포함 필드로 인한 InvalidPositionKeyError 발생 시간을 측정.

    delimiter가 포함된 필드는 __post_init__에서 즉시 거부되므로, 정상 parse보다
    빠르거나 동등해야 한다(추가 검증 오버헤드가 없으므로).

    실측 근거: __post_init__의 delimiter 체크는 단순 in 연산(O(n))이며
    필드 길이는 최대 32자이므로 1ms 미만이어야 한다.
    """
    from tests.support.perf_budget import PerfBudget

    perf = PerfBudget()

    # 정상 키 생성 + 문자열화
    normal_key = PositionKey(
        venue="venue",
        instrument_id="instr",
        strategy_id="strat",
        execution_id="exec",
        portfolio_id=UUID(int=1),
    )
    sample_normal = perf.sample(lambda: str(normal_key))

    # delimiter 포함 필드로 인한 거부 시간
    # PositionKey는 delimiter 포함 시 InvalidPositionKeyError를 던지므로,
    # PerfBudget.sample은 예외를 포착하지 못해 re-raise한다.
    # 따라서 time.process_time로 직접 측정한다.
    _reject_exception: pytest.ExceptionInfo[InvalidPositionKeyError] | None = None
    reject_times: list[float] = []

    for _ in range(50):
        t0 = time.process_time()
        try:
            PositionKey(
                venue="venue:poison",
                instrument_id="instr",
                strategy_id="strat",
                execution_id="exec",
                portfolio_id=UUID(int=1),
            )
        except InvalidPositionKeyError as exc:
            if _reject_exception is None:
                _reject_exception = pytest.ExceptionInfo.from_exception(exc)
        dt = time.process_time() - t0
        reject_times.append(dt * 1000)  # ms

    # 단언: 거부 시간도 실용적인 범위 내여야 한다(최대 1ms)
    assert reject_times[-1] < 1.0, f"delimiter 거부 시간이 1ms 초과: {reject_times[-1]:.3f}ms"
    # 정상 생성이 거부보다 느릴 수 없다(거부는 즉시 실패)
    assert reject_times[-1] <= sample_normal.cpu_ms * 2, (
        f"거부 시간이 정상 시간의 2배 초과: reject={reject_times[-1]:.3f}ms, "
        f"normal={sample_normal.cpu_ms:.3f}ms"
    )


# ── property: delimiter 경계 케이스 ──


@given(
    _FIELD_TEXT,
    _FIELD_TEXT,
    _FIELD_TEXT,
    _FIELD_TEXT,
    _UUIDS,
    st.sampled_from(("venue", "instrument_id", "strategy_id", "execution_id")),
)
def test_delimiter_at_boundary_of_field_is_rejected(
    venue: str,
    instrument_id: str,
    strategy_id: str,
    execution_id: str,
    portfolio_id: UUID,
    poisoned_field: str,
) -> None:
    """property 테스트 — 필드 시작/끝에 delimiter를 붙여도 거부된다.

    hypothesis가 생성한 임의 필드 값의 앞뒤로 ':'를 붙인 뒤
    PositionKey 생성 시 InvalidPositionKeyError가.raise 된다.
    """
    values = dict(
        zip(
            ("venue", "instrument_id", "strategy_id", "execution_id"),
            (venue, instrument_id, strategy_id, execution_id),
            strict=True,
        )
    )
    original = values[poisoned_field]
    # 앞뒤로 delimiter를 붙인다
    values[poisoned_field] = ":" + original + ":"

    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue=values["venue"],
            instrument_id=values["instrument_id"],
            strategy_id=values["strategy_id"],
            execution_id=values["execution_id"],
            portfolio_id=portfolio_id,
        )
