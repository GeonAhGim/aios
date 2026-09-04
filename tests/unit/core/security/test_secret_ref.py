"""secret_ref.py 단위 테스트 — 문자열 왕복 + parse 부정 경로.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-32
(+ §3.6). 핵심 불변: `str(SecretRef.parse(s)) == s`(정규형)이고, 형식·scope·
kind·빈 id/kid 위반은 전부 `SecretRefParseError`로 fail-closed하며 원본
문자열을 메시지에 echo하지 않는다(호출부가 실수로 평문을 담아 부를 가능성).
"""
from __future__ import annotations

import pytest

from src.core.security.secret_ref import SecretRef, SecretRefParseError


def test_str_format_matches_frontend_contract() -> None:
    ref = SecretRef(scope="PAPER", kind="exchange_credential", id="42", kid="v1")
    assert str(ref) == "secref://paper/exchange_credential/42@v1"


def test_parse_round_trips_with_str() -> None:
    raw = "secref://live/mfa_secret/uuid-abc@kid-2"
    ref = SecretRef.parse(raw)
    assert ref == SecretRef(scope="LIVE", kind="mfa_secret", id="uuid-abc", kid="kid-2")
    assert str(ref) == raw


def test_parse_accepts_withdrawal_dest_kind() -> None:
    ref = SecretRef.parse("secref://paper/withdrawal_dest/7@kid-3")
    assert ref.kind == "withdrawal_dest"


def test_parse_rejects_wrong_scheme() -> None:
    with pytest.raises(SecretRefParseError):
        SecretRef.parse("http://paper/exchange_credential/42@kid-1")


def test_parse_rejects_unknown_scope() -> None:
    with pytest.raises(SecretRefParseError):
        SecretRef.parse("secref://staging/exchange_credential/42@kid-1")


def test_parse_rejects_unknown_kind() -> None:
    with pytest.raises(SecretRefParseError):
        SecretRef.parse("secref://paper/api_key/42@kid-1")


def test_parse_rejects_missing_kid() -> None:
    with pytest.raises(SecretRefParseError):
        SecretRef.parse("secref://paper/exchange_credential/42")


def test_parse_rejects_completely_malformed_string() -> None:
    with pytest.raises(SecretRefParseError):
        SecretRef.parse("not-a-secret-ref")
    with pytest.raises(SecretRefParseError):
        SecretRef.parse("")


def test_parse_rejects_empty_id_or_kid() -> None:
    with pytest.raises(SecretRefParseError):
        SecretRef.parse("secref://paper/exchange_credential/@kid-1")
    with pytest.raises(SecretRefParseError):
        SecretRef.parse("secref://paper/exchange_credential/42@")


def test_parse_error_does_not_echo_raw_input() -> None:
    """원본 문자열이 평문을 담고 있을 가능성이 있으므로 메시지에 echo하지
    않는다 — 길이 정보만 남는지 확인."""
    secret_looking_input = "not-a-secret-ref-but-maybe-sk_live_abcdef0123456789"
    with pytest.raises(SecretRefParseError) as exc_info:
        SecretRef.parse(secret_looking_input)
    assert secret_looking_input not in str(exc_info.value)
