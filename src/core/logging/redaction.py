"""108 §2.1 deny-field blocking — pure key/value-based masking functions.

Spec: docs/design/codex/108_structured_logging_and_observability_field_standard_v1.0.md §2.1,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-02.

`redact()` is a pure function (no I/O) that returns a new masked dict without
mutating its input. Value-based patterns (64-char hex, JWT-like `eyJ` prefix,
Korean resident-registration-number-like strings) only mask when the entire
string matches the pattern exactly (fullmatch) — masking an incidental
substring inside a longer sentence would make the log itself meaningless, so
partial-match false positives are avoided. Key matching, as specified, is
substring and case-insensitive (`user_api_key` also matches `api_key`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, Final

REDACTED: Final[str] = "<redacted>"

# 108 §2.1: raw secrets/tokens/decrypted credentials must never land in logs —
# any key that substring-matches one of these (case-insensitive) gets masked
# regardless of the value's shape.
_DEFAULT_DENY_KEYS: set[str] = {
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

# Backwards-compatible alias for callers that import `DENY_KEYS`.
DENY_KEYS = _DEFAULT_DENY_KEYS

# An opaque reference (secret_ref.py) is a value 108 §2.1 treats as safe, so
# it is not masked even if it incidentally matches one of the value-based
# patterns below.
_SECRET_REF_PREFIX: Final[str] = "secref://"

_HEX64_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{64}$")
_JWT_LIKE_RE: Final[re.Pattern[str]] = re.compile(r"^eyJ[A-Za-z0-9_.-]{10,}$")
# Korean resident-registration-number-like: 6 digits (birth date) + optional
# hyphen + 7 digits (gender/region code).
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
    """Return a new masked dict for `payload` — the original is left untouched (pure function).

    A key that substring-matches `DENY_KEYS` is replaced with `<redacted>`
    regardless of the value's shape. Other keys are checked recursively if
    the value is a dict/list, or against the value-based patterns if it is
    a string.
    """
    result: dict[str, Any] = {}
    for key, value in payload.items():
        result[key] = REDACTED if _key_is_denied(key) else _redact_value(value)
    return result


class RedactionFilter(logging.Filter):
    """Adapter attached to a `logging.Handler` — replaces `record.payload`
    (structured extra) with the masked dict returned by `redact()`. `redact()`
    itself stays a pure function; this class only owns the wiring into the
    logging pipeline."""

    def filter(self, record: logging.LogRecord) -> bool:
        payload = getattr(record, "payload", None)
        if isinstance(payload, Mapping):
            record.payload = redact(payload)
        return True
