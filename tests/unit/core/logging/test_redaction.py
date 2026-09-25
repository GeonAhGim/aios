import logging
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import src.core.logging.redaction as redaction_module
from src.core.logging.fields import StructuredLogLine
from src.core.logging.redaction import REDACTED, RedactionFilter, redact

# ADR-2026-09-09-C Decision 1 예산표에 로그 레닥션 전용 항목은 없다. redact()는 로그 라인
# 하나마다 I/O 없이 도는 인프로세스 핫패스라 가장 근접한 항목인 "사전거래 게이트 p99 5ms"
# (§Decision 1)를 차용한다.
_PRETRADE_GATE_P99_BUDGET_SEC = 0.005

_LOG_PAYLOAD_SAMPLE = {
    "order_id": "ord-123",
    "api_key": "abcd1234",
    "user": {"id": 1, "session_token": "tok-xyz"},
    "items": [
        {"sku": "abc", "token": "eyJhbGciOiJIUzI1NiJ9.xx.yy"},
        {"sku": "def"},
    ],
}


def _measure_redact_cycle(n: int) -> list[float]:
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        redact(_LOG_PAYLOAD_SAMPLE)
        samples.append(time.perf_counter() - start)
    return samples


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "API_KEY",
        "user_api_key",
        "secret",
        "Password",
        "totp",
        "TOKEN",
        "private_key",
        "vault_secret_ref",
        "exchange_secret_key",
    ],
)
def test_deny_key_partial_match_case_insensitive(key: str):
    result = redact({key: "s3nsitive-value"})

    assert result[key] == REDACTED


def test_non_deny_key_with_plain_value_passes_through():
    result = redact({"username": "alice", "order_id": 42})

    assert result == {"username": "alice", "order_id": 42}


def test_redacts_secret_nested_inside_dict():
    payload = {"user": {"id": 1, "api_key": "abcd1234"}}

    result = redact(payload)

    assert result["user"]["api_key"] == REDACTED
    assert result["user"]["id"] == 1


def test_redacts_nested_dict_headers_authorization():
    """PLT-02 DEEPEN: 중첩 dict의 sensitive key(예: headers.authorization)도 필터링."""
    payload = {"headers": {"authorization": "Bearer xyz123"}}

    result = redact(payload)

    assert result == {"headers": {"authorization": "<redacted>"}}


def test_redacts_deeply_nested_dict_three_levels():
    """PLT-02 DEEPEN: 3단계 중첩 dict에서도 sensitive key가 redact된다."""
    payload = {"a": {"b": {"c": {"password": "deep_secret"}}}}

    result = redact(payload)

    assert result == {"a": {"b": {"c": {"password": "<redacted>"}}}}


def test_redacts_nested_dict_with_mixed_keys():
    """PLT-02 DEEPEN: 중첩 dict에서 sensitive/non-sensitive 키가 혼합된 경우."""
    payload = {"request": {"method": "GET", "api_key": "sk-123", "path": "/users"}}

    result = redact(payload)

    assert result == {"request": {"method": "GET", "api_key": "<redacted>", "path": "/users"}}


def test_redacts_list_of_dicts_with_sensitive_keys():
    """PLT-02 DEEPEN: list 내부 dict의 sensitive key도 redact된다."""
    payload = {"items": [{"token": "abc"}, {"safe": "value"}]}

    result = redact(payload)

    assert result == {"items": [{"token": "<redacted>"}, {"safe": "value"}]}


def test_redacts_secret_nested_inside_list():
    payload = {"items": [{"token": "abc.def.ghi"}, {"safe": "value"}]}

    result = redact(payload)

    assert result["items"][0]["token"] == REDACTED
    assert result["items"][1]["safe"] == "value"


def test_redacts_secret_inside_list_of_lists():
    payload = {"batches": [[{"authorization": "Bearer xyz"}]]}

    result = redact(payload)

    assert result["batches"][0][0]["authorization"] == REDACTED


def test_accepts_valid_payload():
    """PLT-02 §8 required fields:
    actor.type/actor.tenant_id/actor.id/level/message/correlation_id/event_type/detail/payload."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="test log",
        args=(),
        exc_info=None,
    )

    record.payload = {
        "actor": {"type": "user", "tenant_id": "t1", "id": "USR-001"},
        "level": "info",
        "message": "Order placed successfully",
        "correlation_id": "corr-1",
        "event_type": "order.placed",
        "detail": {"order_id": "ORD-42"},
        "payload": {"sku": "WIDGET", "qty": 3},
    }

    RedactionFilter().filter(record)

    # all 8 required fields must survive redaction
    assert record.payload["actor"]["type"] == "user"
    assert record.payload["actor"]["tenant_id"] == "t1"
    assert record.payload["actor"]["id"] == "USR-001"
    assert record.payload["level"] == "info"
    assert record.payload["message"] == "Order placed successfully"
    assert record.payload["correlation_id"] == "corr-1"
    assert record.payload["event_type"] == "order.placed"
    assert record.payload["detail"]["order_id"] == "ORD-42"
    assert record.payload["payload"]["sku"] == "WIDGET"


def test_original_payload_is_unchanged_after_redact():
    original = {"user": {"api_key": "abcd1234", "id": 1}, "items": [{"token": "abc"}]}
    snapshot = {"user": {"api_key": "abcd1234", "id": 1}, "items": [{"token": "abc"}]}

    redact(original)

    assert original == snapshot


def test_hex64_value_is_redacted_regardless_of_key_name():
    value = "a" * 64
    result = redact({"note": value})

    assert result["note"] == REDACTED


def test_jwt_like_value_is_redacted_regardless_of_key_name():
    value = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ"
    result = redact({"note": value})

    assert result["note"] == REDACTED


def test_resident_registration_number_like_value_is_redacted():
    result = redact({"note": "901231-1234567"})
    assert result["note"] == REDACTED

    result_no_hyphen = redact({"note": "9012311234567"})
    assert result_no_hyphen["note"] == REDACTED


def test_secref_value_passes_through_unredacted():
    value = "secref://paper/exchange_credential/123@v2"
    result = redact({"credential": value})

    assert result["credential"] == value


def test_key_containing_secret_substring_is_redacted_even_when_value_is_a_safe_ref():
    # Keys like vault_secret_ref (src/foundation/connections) partial-match "secret"
    # and get masked even when the value is an opaque reference (secref://). This is
    # over-masking, not a leak, so it's not a blocker -- pin the behavior here.
    value = "secref://paper/exchange_credential/123@v2"
    result = redact({"vault_secret_ref": value})

    assert result["vault_secret_ref"] == REDACTED


# --- 부분 문자열 오탐 방지: 값 패턴은 전체 문자열이 정확히 일치할 때만 마스킹한다.


def test_hex_like_substring_inside_longer_text_is_not_redacted():
    value = "commit " + "a" * 64 + " landed"  # 64-hex가 문장 속 일부일 뿐 값 전체가 아님
    result = redact({"note": value})

    assert result["note"] == value


def test_63_char_hex_is_not_treated_as_secret():
    value = "a" * 63
    result = redact({"note": value})

    assert result["note"] == value


def test_jwt_like_substring_inside_sentence_is_not_redacted():
    value = "token decode failed near eyJhbGciOiJIUzI1NiJ9 boundary"
    result = redact({"note": value})

    assert result["note"] == value


def test_digit_string_with_wrong_length_is_not_treated_as_rrn():
    result = redact({"note": "1234567890"})  # 10자리 — 13자리 RRN 형식이 아님

    assert result["note"] == "1234567890"


def test_rrn_like_substring_inside_longer_text_is_not_redacted():
    value = "ref 901231-1234567 archived"
    result = redact({"note": value})

    assert result["note"] == value


def test_redaction_filter_masks_record_payload_in_place():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="x",
        args=(),
        exc_info=None,
    )
    record.payload = {"api_key": "abcd1234", "note": "ok"}

    result = RedactionFilter().filter(record)

    assert result is True
    assert record.payload == {"api_key": REDACTED, "note": "ok"}


def test_redact_raises_when_payload_is_not_a_mapping():
    """negative — redact()는 Mapping만 받는다. 잘못된 타입(list)을 넘기면 조용히 빈 dict나
    부분 결과를 돌려주지 말고 즉시 실패해야 한다(fail-closed) — 그래야 호출부가 "마스킹된
    빈 결과"를 "원래 secret이 없었다"로 오해해 원본을 그대로 로깅하는 사고를 막는다."""
    with pytest.raises(AttributeError):
        redact(["not", "a", "mapping"])


def test_redact_failure_during_key_check_propagates_instead_of_silently_leaking():
    """실패 주입 — 키 검사(_key_is_denied)가 예외를 내면 redact()는 부분적으로만 마스킹된
    (즉 일부 secret이 그대로 남은) dict를 반환하지 말고 예외를 그대로 전파해야 한다.
    조용히 삼키면 호출부가 이 미완성 dict를 그대로 로깅해 secret이 새어나갈 수 있다."""
    payload = {"api_key": "abcd1234", "safe": "value"}

    with patch("src.core.logging.redaction._key_is_denied", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            redact(payload)


def test_redact_meets_pretrade_gate_budget():
    """수치 성능 단언 — redact()는 구조화 로그 한 줄마다 호출되는 인프로세스 핫패스다.
    p99가 예산 안에 들어오는지 확인한다(예산 근거는 모듈 상단 주석)."""
    samples = sorted(_measure_redact_cycle(500))
    p99 = samples[int(len(samples) * 0.99)]

    assert p99 < _PRETRADE_GATE_P99_BUDGET_SEC


def test_redact_budget_assertion_catches_regression():
    """게이트 적색 재현 — 키 검사 경로에 10ms 인위 지연을 주입해 위 p99 예산 단언이 실제로
    AssertionError를 내는지 확인한다(타우톨로지가 아님을 증명)."""
    real_key_is_denied = redaction_module._key_is_denied

    def _slow_key_is_denied(key: str) -> bool:
        time.sleep(0.01)
        return real_key_is_denied(key)

    with patch("src.core.logging.redaction._key_is_denied", side_effect=_slow_key_is_denied):
        samples = sorted(_measure_redact_cycle(20))
    p99 = samples[int(len(samples) * 0.99)]

    with pytest.raises(AssertionError):
        assert p99 < _PRETRADE_GATE_P99_BUDGET_SEC


def test_multiple_deny_keys_share_identical_redacted_value_but_key_identity_preserved():
    """DEEPEN: REDACTED 상수는 모든 deny key에 동일한 값(`<redacted>`)으로 적용된다 —
    값만 보면 어떤 필드가 마스킹됐는지 구분할 수 없다. 하지만 `redact()`는 dict의 key를
    그대로 보존하므로(값을 지우되 key는 지우지 않음) "어떤 필드가 redact됐는지"는 값이
    아니라 key로 식별한다. 이 계약을 명시적으로 고정한다 — key까지 지우거나 필드별로
    다른 placeholder를 쓰는 변경은 이 테스트를 깨야 한다."""
    payload = {"api_key": "k1", "secret": "s1", "password": "p1", "safe": "ok"}

    result = redact(payload)

    assert result["api_key"] == REDACTED
    assert result["secret"] == REDACTED
    assert result["password"] == REDACTED
    assert result["safe"] == "ok"
    # 값 자체는 서로 구분 불가능하지만, key 집합은 원본과 동일하게 보존된다.
    assert result["api_key"] == result["secret"] == result["password"]
    assert set(result.keys()) == set(payload.keys())


def test_redaction_filter_is_noop_when_payload_absent():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="x",
        args=(),
        exc_info=None,
    )

    assert RedactionFilter().filter(record) is True
    assert not hasattr(record, "payload") or record.payload == {}


@pytest.mark.parametrize("non_mapping_payload", [None, ["a", "b"], "raw-string", 42])
def test_redaction_filter_does_not_raise_on_non_mapping_payload(non_mapping_payload):
    """PLT-02 DEEPEN negative — filter()는 record.payload가 dict가 아니어도(None,
    list, str, int) AttributeError를 내지 않는다. Mapping이 아니면 마스킹을 건너뛰고
    원래 값을 그대로 둔다 — 로깅 파이프라인 전체를 죽이는 것보다 안전하다."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="x",
        args=(),
        exc_info=None,
    )
    record.payload = non_mapping_payload

    result = RedactionFilter().filter(record)

    assert result is True
    assert record.payload == non_mapping_payload


# ---------------------------------------------------------------------------
# PLT-02: StructuredLogLine — level-only validation (deepen)
# ---------------------------------------------------------------------------


class TestStructuredLogLineRejectsUnknownLevel:
    """StructuredLogLine must reject unknown log levels (PLT-02)."""

    def test_rejects_unknown_level_string(self) -> None:
        """An unknown level string must raise ValueError."""
        with pytest.raises(ValueError):
            StructuredLogLine(
                timestamp=datetime.now(timezone.utc),
                level="critical",
                message="test message",
                component="test",
                event="test_event",
                tenant_id="tenant-1",
                trace_id="trace-1",
                duration_ms=10,
                actor_subject_id="user-abc",
            )

    def test_rejects_empty_level(self) -> None:
        """An empty string for level must raise ValueError."""
        with pytest.raises(ValueError):
            StructuredLogLine(
                timestamp=datetime.now(timezone.utc),
                level="",
                message="test message",
                component="test",
                event="test_event",
                tenant_id="tenant-1",
                trace_id="trace-1",
                duration_ms=10,
                actor_subject_id="user-abc",
            )

    def test_accepts_valid_levels(self) -> None:
        """All valid levels must be accepted without error."""
        for valid_level in ("debug", "info", "warn", "error"):
            log = StructuredLogLine(
                timestamp=datetime.now(timezone.utc),
                level=valid_level,
                message="test message",
                component="test",
                event="test_event",
                tenant_id="tenant-1",
                trace_id="trace-1",
                duration_ms=10,
                actor_subject_id="user-abc",
            )
            assert log.level == valid_level


class TestStructuredLogLineRejectsMissingActorSubjectId:
    """StructuredLogLine must reject records missing actor_subject_id (PLT-02)."""

    def test_rejects_missing_actor_subject_id(self) -> None:
        """actor_subject_id is a required field — omitting it raises ValueError."""
        with pytest.raises(ValueError):
            StructuredLogLine(
                timestamp=datetime.now(timezone.utc),
                level="info",
                message="test message",
                component="test",
                event="test_event",
                tenant_id="tenant-1",
                trace_id="trace-1",
                duration_ms=10,
            )

    def test_rejects_none_actor_subject_id(self) -> None:
        """actor_subject_id=None must be rejected (not silently stored as 'None')."""
        with pytest.raises(ValueError):
            StructuredLogLine(
                timestamp=datetime.now(timezone.utc),
                level="info",
                message="test message",
                component="test",
                event="test_event",
                tenant_id="tenant-1",
                trace_id="trace-1",
                duration_ms=10,
                actor_subject_id=None,  # pyright-ignore: None is intentional
            )
