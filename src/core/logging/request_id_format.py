"""Request-id format rules: which client-supplied X-Request-ID values are accepted
and how they map onto the UUID used as the envelope ``trace_id``.

Accepted: 32-hex, hyphenated UUID, or a 26-char ULID (Crockford base32, case-insensitive,
first char <= 7 so the value fits 128 bits); total length <= 128.
"""

from __future__ import annotations

import re
from uuid import UUID

MAX_REQUEST_ID_LEN = 128

_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")
_UUID_HYPHEN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{25}$")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def is_valid_request_id(raw: str) -> bool:
    if not raw or len(raw) > MAX_REQUEST_ID_LEN:
        return False
    return bool(_HEX32.match(raw) or _UUID_HYPHEN.match(raw) or _ULID.match(raw))


def request_id_to_uuid(raw: str) -> UUID:
    """UUID/hex pass through; a ULID decodes to its 128-bit integer. Raises ValueError otherwise."""
    if _HEX32.match(raw) or _UUID_HYPHEN.match(raw):
        return UUID(raw)
    if _ULID.match(raw):
        value = 0
        for ch in raw.upper():
            value = (value << 5) | _CROCKFORD.index(ch)
        return UUID(int=value)
    raise ValueError("unsupported request id format")
