"""Request-ID format validation and request-ID -> UUID mapping (pure, no I/O).

Accepted forms (total length <= 128): 32-char hex, hyphenated UUID, or a
26-char ULID (Crockford base32, case-insensitive, first char <= '7' so the
value fits in 128 bits).
"""

from __future__ import annotations

import re
from uuid import UUID

MAX_REQUEST_ID_LENGTH = 128

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_RE = re.compile(r"[0-7][0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{25}")
_HEX32_RE = re.compile(r"[0-9a-fA-F]{32}")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def is_valid_request_id(raw: str) -> bool:
    if not raw or len(raw) > MAX_REQUEST_ID_LENGTH:
        return False
    return bool(_HEX32_RE.fullmatch(raw) or _UUID_RE.fullmatch(raw) or _ULID_RE.fullmatch(raw))


def request_id_to_uuid(raw: str) -> UUID:
    """Map a valid request id to a UUID; raises ValueError when invalid."""
    if not is_valid_request_id(raw):
        raise ValueError("invalid request id")
    if _ULID_RE.fullmatch(raw) and not _HEX32_RE.fullmatch(raw):
        value = 0
        for ch in raw.upper():
            value = (value << 5) | _CROCKFORD.index(ch)
        return UUID(int=value)
    return UUID(hex=raw.replace("-", ""))
