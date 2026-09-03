import logging

import pytest

from src.core.logging.redaction import REDACTED, RedactionFilter, redact


@pytest.mark.parametrize(
    "key",
    ["api_key", "API_KEY", "user_api_key", "secret", "Password", "totp", "TOKEN", "private_key"],
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


def test_redacts_secret_nested_inside_list():
    payload = {"items": [{"token": "abc.def.ghi"}, {"safe": "value"}]}

    result = redact(payload)

    assert result["items"][0]["token"] == REDACTED
    assert result["items"][1]["safe"] == "value"


def test_redacts_secret_inside_list_of_lists():
    payload = {"batches": [[{"authorization": "Bearer xyz"}]]}

    result = redact(payload)

    assert result["batches"][0][0]["authorization"] == REDACTED


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
