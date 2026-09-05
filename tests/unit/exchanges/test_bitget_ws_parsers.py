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
    message = {"event": "error", "code": "30001", "msg": "channel does not exist",
               "arg": {"channel": "nope"}, "secret": "SHOULD-NOT-LEAK"}
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
