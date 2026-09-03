"""108 §2.1 금지 필드 차단 — 키/값 기반 마스킹 순수 함수.

Spec: docs/design/codex/108_structured_logging_and_observability_field_standard_v1.0.md §2.1,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-02.

`redact()`는 입력을 변경하지 않고 마스킹된 새 dict를 반환하는 순수 함수다(I/O 없음).
값 기반 패턴(64자 hex, JWT-유사 `eyJ` 접두, 주민등록번호류)은 전체 문자열이 패턴과
정확히 일치할 때만 마스킹한다(fullmatch) — 긴 문장 속 우연한 부분 문자열까지
지워버리면 로그 자체가 무의미해지므로 부분 일치 오탐을 피한다. 키 매칭은 스펙이
명시한 대로 부분 일치·대소문자 무시다(`user_api_key`도 `api_key`로 걸린다).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, Final

REDACTED: Final[str] = "<redacted>"

# 108 §2.1: 원문 secret/토큰/복호화 credential은 절대 로그에 남기지 않는다 —
# 이 키들과 부분 일치(대소문자 무시)하면 값 형태와 무관하게 마스킹한다.
DENY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "api_secret",
        "secret",
        "password",
        "totp",
        "token",
        "authorization",
        "private_key",
        "raw_payload",
        "answers",
    }
)

# opaque reference(secret_ref.py)는 108 §2.1이 허용하는 안전한 값이므로
# 아래 값 기반 패턴에 우연히 걸려도 마스킹하지 않는다.
_SECRET_REF_PREFIX: Final[str] = "secref://"

_HEX64_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{64}$")
_JWT_LIKE_RE: Final[re.Pattern[str]] = re.compile(r"^eyJ[A-Za-z0-9_.-]{10,}$")
# 한국 주민등록번호류: 6자리(생년월일) + 선택적 하이픈 + 7자리(성별·지역 코드).
_RRN_LIKE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{6}-?\d{7}$")


def _key_is_denied(key: str) -> bool:
    lowered = key.lower()
    return any(deny in lowered for deny in DENY_KEYS)


def _value_matches_secret_pattern(value: str) -> bool:
    if value.startswith(_SECRET_REF_PREFIX):
        return False
    return bool(_HEX64_RE.match(value) or _JWT_LIKE_RE.match(value) or _RRN_LIKE_RE.match(value))


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str) and _value_matches_secret_pattern(value):
        return REDACTED
    return value


def redact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """`payload`를 마스킹한 새 dict를 반환한다 — 원본은 그대로 둔다(순수 함수).

    키가 `DENY_KEYS`와 부분 일치하면 값의 형태와 무관하게 `<redacted>`로
    바꾼다. 그 외 키는 값이 dict/list면 재귀적으로, 문자열이면 값 기반 패턴으로
    검사한다.
    """
    result: dict[str, Any] = {}
    for key, value in payload.items():
        result[key] = REDACTED if _key_is_denied(key) else _redact_value(value)
    return result


class RedactionFilter(logging.Filter):
    """`logging.Handler`에 부착하는 어댑터 — `record.payload`(구조화 extra)를
    `redact()`로 마스킹한 새 dict로 교체한다. `redact()` 자체는 순수 함수로
    남기고, 로깅 파이프라인과의 연결만 이 클래스가 담당한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        payload = getattr(record, "payload", None)
        if isinstance(payload, Mapping):
            record.payload = redact(payload)
        return True
