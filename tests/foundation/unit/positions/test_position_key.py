"""LB-2/FA-0d — position_key 직렬화·파싱 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2,
docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d
(`venue:instrument_id:strategy_id:execution_id:portfolio_id` 5부분 형식).

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 원 task-1943(commit
278f6227)의 D3 하한 미달로 지적한 두 공백 중 리플레이 증거와 성능 단언을
`test_parse_str_round_trip_is_deterministic_across_repeated_replays`와
`test_construction_and_round_trip_throughput_stays_within_budget`로 여기서
메운다(task-3032). 적대적/동시성 증거는 I/O가 필요해
tests/foundation/integration/positions/test_position_key_adversarial.py에 둔다.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import pytest

from src.foundation.positions.domain.position_key import (
    InvalidPositionKeyError,
    PositionKey,
)
from tests.conftest import PerfBudget

_PORTFOLIO_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_str_serializes_five_parts_with_colon_delimiter() -> None:
    key = PositionKey(
        venue="bitget",
        instrument_id="BTC/USDT",
        strategy_id="strat-1",
        execution_id="exec-1",
        portfolio_id=_PORTFOLIO_ID,
    )
    assert str(key) == f"bitget:BTC/USDT:strat-1:exec-1:{_PORTFOLIO_ID}"


def test_parse_round_trips_with_str() -> None:
    raw = f"bitget:BTC/USDT:strat-1:exec-1:{_PORTFOLIO_ID}"
    key = PositionKey.parse(raw)
    assert key == PositionKey(
        venue="bitget",
        instrument_id="BTC/USDT",
        strategy_id="strat-1",
        execution_id="exec-1",
        portfolio_id=_PORTFOLIO_ID,
    )
    assert str(key) == raw


def test_parse_rejects_wrong_field_count() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse(f"bitget:BTC/USDT:strat-1:{_PORTFOLIO_ID}")

    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse(f"bitget:BTC/USDT:strat-1:exec-1:{_PORTFOLIO_ID}:extra")


def test_parse_rejects_legacy_four_part_key() -> None:
    """FA-0d 이전 4부분 형식은 더 이상 유효하지 않다 — portfolio_id 없는
    레거시 키가 조용히 통과하면 안 된다(fail-closed)."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("bitget:BTC/USDT:strat-1:exec-1")


def test_parse_rejects_non_uuid_portfolio_id() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("bitget:BTC/USDT:strat-1:exec-1:not-a-uuid")


def test_parse_rejects_empty_raw_string() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("")


def test_construct_rejects_empty_component() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="",
            instrument_id="BTC/USDT",
            strategy_id="strat-1",
            execution_id="exec-1",
            portfolio_id=_PORTFOLIO_ID,
        )


def test_construct_rejects_component_containing_delimiter() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="bitget:extra",
            instrument_id="BTC/USDT",
            strategy_id="strat-1",
            execution_id="exec-1",
            portfolio_id=_PORTFOLIO_ID,
        )


def test_construct_rejects_non_uuid_portfolio_id() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="bitget",
            instrument_id="BTC/USDT",
            strategy_id="strat-1",
            execution_id="exec-1",
            portfolio_id=cast(UUID, "not-a-uuid"),
        )


def test_position_key_is_frozen_and_hashable() -> None:
    key = PositionKey(
        venue="bitget",
        instrument_id="BTC/USDT",
        strategy_id="strat-1",
        execution_id="exec-1",
        portfolio_id=_PORTFOLIO_ID,
    )
    with pytest.raises(AttributeError):
        key.venue = "kis"  # type: ignore[misc]  # negative test: frozen dataclass 재할당 금지를 AttributeError로 검증
    assert hash(key) == hash(
        PositionKey(
            venue="bitget",
            instrument_id="BTC/USDT",
            strategy_id="strat-1",
            execution_id="exec-1",
            portfolio_id=_PORTFOLIO_ID,
        )
    )


def test_different_portfolio_id_yields_different_key() -> None:
    """FA-0d의 핵심 불변: venue/instrument/strategy/execution이 같아도
    portfolio_id가 다르면 서로 다른 포지션이다."""
    key_a = PositionKey(
        venue="bitget",
        instrument_id="BTC/USDT",
        strategy_id="strat-1",
        execution_id="exec-1",
        portfolio_id=_PORTFOLIO_ID,
    )
    key_b = PositionKey(
        venue="bitget",
        instrument_id="BTC/USDT",
        strategy_id="strat-1",
        execution_id="exec-1",
        portfolio_id=uuid4(),
    )
    assert key_a != key_b
    assert str(key_a) != str(key_b)


def test_parse_str_round_trip_is_deterministic_across_repeated_replays() -> None:
    """리플레이 증거 -- `pos_snapshot.position_key`는 PRIMARY KEY 문자열이라
    같은 원본 키를 반복 파싱/재직렬화(예: 저널 replay·재구축, [[rebuild_snapshot]])
    했을 때 단 한 바이트라도 흔들리면 조회 실패/데이터 분기로 이어진다.
    같은 raw 문자열을 200회 반복 parse -> str 하여 매번 완전히 동일한
    문자열을 내는지(비결정성 없음) 검증한다."""
    raw = f"bitget:BTC/USDT:strat-1:exec-1:{_PORTFOLIO_ID}"
    replays = [str(PositionKey.parse(raw)) for _ in range(200)]
    assert all(replayed == raw for replayed in replays)
    assert len(set(replays)) == 1


@pytest.mark.perf
def test_construction_and_round_trip_throughput_stays_within_budget(
    perf_budget: PerfBudget,
) -> None:
    """수치 성능 단언 -- DEPTH 재감사(task-2724)가 지적한 공백을 메운다.
    이 순수 값객체는 I/O가 없어 실DB 왕복 예산(task-3030/905 관례)이 아니라
    처리량(ops/sec) 하한을 건다 -- `record_fill`/`rebuild_snapshot`/
    `record_funding_fee`가 매 호출마다 `PositionKey.parse()`로 이 경로를
    타므로(중앙 생성자 강제), 순수 파싱조차 병목이면 그 상위 핫패스 전부가
    영향을 받는다. task-7434: wall-clock perf_counter() 대신 공용
    perf_budget(process_time 기반)으로 측정한다."""
    n = 20_000
    budget_ms = 2000.0
    min_ops_per_sec = 50_000.0

    def _run_once() -> None:
        for i in range(n):
            key = PositionKey(
                venue="bitget",
                instrument_id=f"INST{i}",
                strategy_id="strat-1",
                execution_id="exec-1",
                portfolio_id=_PORTFOLIO_ID,
            )
            parsed = PositionKey.parse(str(key))
            assert parsed == key

    sample = perf_budget.assert_within(
        _run_once, budget_ms=budget_ms, label=f"{n} PositionKey construct+str+parse"
    )
    ops_per_sec = n / (sample.cpu_ms / 1000)

    print(
        f"[task-1943/3032 PositionKey construct+str+parse] {n} rounds cpu={sample.cpu_ms:.1f}ms "
        f"({ops_per_sec:.0f} ops/s, budget<{budget_ms:.0f}ms, min>{min_ops_per_sec:.0f} ops/s)"
    )
    assert ops_per_sec > min_ops_per_sec, (
        f"PositionKey round-trip 처리량이 최소값({min_ops_per_sec:.0f} ops/s)에 "
        f"못 미칩니다({ops_per_sec:.0f})."
    )
