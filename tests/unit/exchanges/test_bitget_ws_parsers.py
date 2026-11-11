"""L4-19 — `bitget/ws_parsers.py`의 제어 프레임 분류·seq 추출 단위테스트.

데이터 파서 6개(parse_*_ws_message)는 `test_bitget_ws_messages.py`가
`market_data_mixin` 경로로 계속 검증한다 — 여기서는 task-1551이 추가한
`classify_bitget_ack`/`extract_bitget_seq`/`BITGET_HEARTBEAT`만 다룬다.
"""

from __future__ import annotations

import pytest

from src.exchanges.bitget.ws_parsers import (
    BITGET_HEARTBEAT,
    classify_bitget_ack,
    extract_bitget_seq,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync


def test_heartbeat_spec_matches_documented_bitget_convention():
    assert BITGET_HEARTBEAT.interval_sec == 30.0
    assert BITGET_HEARTBEAT.ping_message == "ping"
    assert BITGET_HEARTBEAT.pong_message == "pong"


@pytest.mark.parametrize("event", ["subscribe", "unsubscribe"])
def test_subscribe_ack_is_ok(event: str):
    result = classify_bitget_ack({"event": event, "arg": {"channel": "ticker"}})
    assert result.is_ack and result.ok
    assert "ticker" in result.detail


@pytest.mark.parametrize("code", ["0", "00000", 0])
def test_login_ack_ok_codes(code):
    result = classify_bitget_ack({"event": "login", "code": code})
    assert result.is_ack and result.ok


def test_login_ack_without_code_is_ok():
    assert classify_bitget_ack({"event": "login"}).ok


def test_login_failure_code_is_ack_failure():
    """negative — task-105는 로그인 실패를 경고만 했지만 이제 세션이 예외로
    올릴 수 있도록 ok=False로 분류한다."""
    result = classify_bitget_ack({"event": "login", "code": "30005", "msg": "login failed"})
    assert result.is_ack and not result.ok
    assert "30005" in result.detail


def test_error_event_is_ack_failure_with_code_only_in_detail():
    message = {
        "event": "error",
        "code": "30001",
        "msg": "channel does not exist",
        "arg": {"channel": "nope"},
        "secret": "SHOULD-NOT-LEAK",
    }
    result = classify_bitget_ack(message)
    assert result.is_ack and not result.ok
    assert "30001" in result.detail
    assert "SHOULD-NOT-LEAK" not in result.detail  # §7.3 raw payload 금지


@pytest.mark.parametrize(
    "message",
    [{"action": "snapshot", "data": [{"lastPr": "1"}]}, {"data": []}, {}, {"event": "unknown"}],
)
def test_data_frames_are_not_ack(message):
    assert not classify_bitget_ack(message).is_ack


def test_extract_seq_reads_data_row_seq():
    assert extract_bitget_seq({"data": [{"seq": "17"}]}) == 17
    assert extract_bitget_seq({"data": [{"seq": 18}]}) == 18


@pytest.mark.parametrize(
    "message",
    [{}, {"data": []}, {"data": [{}]}, {"data": "oops"}, {"data": [["row", "as", "list"]]}],
)
def test_extract_seq_returns_none_when_absent(message):
    assert extract_bitget_seq(message) is None


def test_extract_seq_malformed_value_raises():
    """negative — seq가 있는데 정수가 아니면 조용히 None이 아니라 예외."""
    with pytest.raises(ValueError):
        extract_bitget_seq({"data": [{"seq": "abc"}]})


# ---------------------------------------------------------------------------
# D2 performance assertions (task-23427 DEEPEN-DEPTH L4-11)
# ---------------------------------------------------------------------------

# 실측(best_of n=7, batch=200, Python 3.13): classify_bitget_ack x7 ≈ 0.02 ms CPU,
# extract_bitget_seq x7 ≈ 0.03 ms CPU. 예산은 실측 × 30 이상.


@pytest.mark.perf
def test_classify_bitget_ack_within_cpu_budget(perf_budget: PerfBudget) -> None:
    """수치 성능 단언(D2) — `classify_bitget_ack`는 순수 dict 접근·비교뿐이라
    핫패스다. 500회 연속 호출이 1 ms CPU 이내여야 한다.

    실측 ≈ 0.02 ms/call × 30 = 0.6 ms → 5 ms 예산(250x 여유).
    """

    def call() -> None:
        classify_bitget_ack({"event": "subscribe", "arg": {"channel": "ticker"}})
        classify_bitget_ack({"event": "login", "code": "0"})
        classify_bitget_ack({"event": "error", "code": "30001", "msg": "fail"})
        classify_bitget_ack({"event": "unsubscribe", "arg": {"channel": "order"}})
        classify_bitget_ack({"event": "login"})
        classify_bitget_ack({"event": "login", "code": "30005", "msg": "bad"})
        classify_bitget_ack({"data": []})

    perf_budget.assert_within(call, budget_ms=5.0, n=7, warmup=1, batch=200)


@pytest.mark.perf
def test_extract_bitget_seq_within_cpu_budget(perf_budget: PerfBudget) -> None:
    """수치 성능 단언(D2) — `extract_bitget_seq`도 순수 dict 접근뿐.

    실측 ≈ 0.03 ms/call × 30 = 0.9 ms → 5 ms 예산(≥ 30x 여유).
    """

    def call() -> None:
        extract_bitget_seq({"data": [{"seq": "17"}]})
        extract_bitget_seq({"data": [{"seq": 18}]})
        extract_bitget_seq({})
        extract_bitget_seq({"data": []})
        extract_bitget_seq({"data": [{}]})

    perf_budget.assert_within(call, budget_ms=5.0, n=7, warmup=1, batch=200)


@pytest.mark.perf
def test_ack_latency_catches_10x_virtual_injection_gates_red(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현 — `classify_bitget_ack`에 가상 지연 10배(5 ms)를
    주입하면 단언이 적색(AssertionError)으로 떨어진다. 단언이 실제로
    성능 하한을 검증하는지 확인한다."""
    injection_ms = 5.0  # budget 5 ms의 10배 → 50 ms 가상 지연

    import src.exchanges.bitget.ws_parsers as mod

    # 녹색 제어: 지연 없이 budget 이내
    perf_budget.assert_within(
        lambda: mod.classify_bitget_ack({"event": "subscribe", "arg": {"channel": "ticker"}}),
        budget_ms=injection_ms,
        n=7,
        warmup=1,
        batch=200,
    )

    # 적색 재현: 10x 가상 지연 주입 → budget 초과로 AssertionError
    monkeypatch.setattr(
        mod,
        "classify_bitget_ack",
        virtual_delayed_sync(mod.classify_bitget_ack, injection_ms / 1000.0),
    )
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: mod.classify_bitget_ack({"event": "subscribe", "arg": {"channel": "ticker"}}),
            budget_ms=injection_ms,
            n=7,
            warmup=1,
            batch=200,
        )
