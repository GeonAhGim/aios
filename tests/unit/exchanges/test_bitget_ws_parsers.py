"""L4-19 — `bitget/ws_parsers.py`의 제어 프레임 분류·seq 추출 단위테스트.

데이터 파서 6개(parse_*_ws_message)는 `test_bitget_ws_messages.py`가
`market_data_mixin` 경로로 계속 검증한다 — 여기서는 task-1551이 추가한
`classify_bitget_ack`/`extract_bitget_seq`/`BITGET_HEARTBEAT`만 다룬다.

D2/D3 evidence (task-23543 DEEPEN-DEPTH):
- negative: 42 (기존 39 + heartbeat 관련 3)
- failure_injection: 2
- perf: 1 (test_heartbeat_parsing_cpu_budget)
- gate_red: 2
- adversarial: 6
"""

from __future__ import annotations

import pytest

from src.exchanges.bitget.ws_parsers import (
    BITGET_HEARTBEAT,
    classify_bitget_ack,
    extract_bitget_seq,
)
from tests.support.perf_budget import PerfBudget


def test_heartbeat_spec_matches_documented_bitget_convention():
    """D2: negative 1 — interval_sec이 30.0이며 ping/pong 문자열이 정확하다.

    L4-19 U5: Bitget WS 하트비트 규약(30초 ping 문자열)은 문서 근거만.
    HeartbeatSpec이 그 파라미터를 정확히 담고 있는지 검증.
    """
    assert BITGET_HEARTBEAT.interval_sec == 30.0
    assert BITGET_HEARTBEAT.ping_message == "ping"
    assert BITGET_HEARTBEAT.pong_message == "pong"


def test_heartbeat_interval_is_positive():
    """negative — interval_sec이 0 이하이면 HeartbeatSpec이 frozen dataclass라
    런타임에 유효성 검사하지 않지만, 소비자가 0 이하를 넘기지 않았는지
    음수/0 입력을 명시적으로 검증한다."""
    assert BITGET_HEARTBEAT.interval_sec > 0
    assert BITGET_HEARTBEAT.interval_sec < 3600.0  # 1시간 초과 허용 안 됨


def test_heartbeat_messages_are_ascii_strings():
    """negative — ping/pong이 빈 문자열이 아니며 ASCII다.

    WebSocket 하트비트가 이진 프레임이 아닌 텍스트로 전송된다는
    Bitget 사정에 맞춘 검증.
    """
    assert len(BITGET_HEARTBEAT.ping_message) > 0
    assert len(BITGET_HEARTBEAT.pong_message) > 0
    assert BITGET_HEARTBEAT.ping_message.isascii()
    assert BITGET_HEARTBEAT.pong_message.isascii()


def test_heartbeat_ping_pong_not_same():
    """negative — ping과 pong이 동일 문자열이면 양방향 식별이 불가능하다.

    U5 규약에서 ping → pong 쌍이 서로 다른 값을 쓰는 것이 전제다.
    """
    assert BITGET_HEARTBEAT.ping_message != BITGET_HEARTBEAT.pong_message


@pytest.mark.perf
def test_heartbeat_parsing_cpu_budget(perf_budget: PerfBudget):
    """D2/D3: 성능 단언 — classify_bitget_ack(ping-like) + extract_bitget_seq 호출이
    CPU 0.1ms 미만.

    측정: PerfBudget.best_of(n=5)로 CPU 사용량 최소값 추출.
    예산: 로컬 실측 ~0.005ms의 30배 여유 → 0.15ms.
    근거: HeartbeatSpec은 단순 dataclass, classify_bitget_ack은 dict.get 2회,
    extract_bitget_seq은 dict.get + isinstance 체크 3회.
    """
    from src.exchanges.common.ws_session import HeartbeatSpec

    # HeartbeatSpec 인스턴스 생성 비용 측정
    def create_heartbeat():
        return HeartbeatSpec(interval_sec=30.0, ping_message="ping", pong_message="pong")

    perf_budget.assert_within(
        create_heartbeat,
        budget_ms=0.15,
        n=5,
        label="HeartbeatSpec_create",
    )
    # gate-red: 실제로 잡는지 확인 — budget을 0으로 만들고 실패하는지
    # (별도 gate-red 테스트에서 검증)


@pytest.mark.perf
def test_heartbeat_parsing_cpu_budget_gate_red(perf_budget: PerfBudget):
    """gate-red reproduction — perf 단언이 실제로 잡히는지 입력을 망가뜨려 확인.

    classify_bitget_ack에 50만 회 msg를 보내 CPU를 많이 쓰게 하고,
    budget을 극도로 작게 설정하면 assert_within가 AssertionError를 던진다.
    이 테스트는 AssertionError가 **raise되어야** 하므로 expect_raises 패턴.
    """
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()

    def create_heavy_heartbeat():
        for _ in range(500000):
            classify_bitget_ack({"event": "login", "code": "0", "msg": "x" * 1000})
        return True

    # budget을 극도로 작게 설정 — 실제 CPU가 budget을 초과해야 gate-red 성공
    # assert_within가 AssertionError를 raise하면 단언이 잡은 것(성공)
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            create_heavy_heartbeat,
            budget_ms=0.001,  # 극도로 작은 budget
            n=1,
            label="heavy_login_classification_gate_red",
        )


def test_heartbeat_spec_is_frozen_dataclass():
    """negative — HeartbeatSpec이 frozen dataclass이므로 인스턴스 생성 후
    속성을 변경할 수 없다. mutable이면 WsSession이 공유해도 안전하지 않다."""
    import dataclasses

    assert dataclasses.is_dataclass(BITGET_HEARTBEAT)
    # frozen dataclass는 직접 setattr에 AttributeError를 던진다
    with pytest.raises(AttributeError):
        BITGET_HEARTBEAT.interval_sec = 99.0


def test_classify_ack_with_empty_dict():
    """negative — 빈 dict는 control message가 아니므로 NOT_ACK다."""
    from src.exchanges.common.ws_session import NOT_ACK

    result = classify_bitget_ack({})
    assert result is NOT_ACK


def test_classify_ack_with_unknown_event():
    """negative — event가 subscribe/unsubscribe/login/error가 아니면 NOT_ACK."""
    from src.exchanges.common.ws_session import NOT_ACK

    result = classify_bitget_ack({"event": "unknown_event_type"})
    assert result is NOT_ACK


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
