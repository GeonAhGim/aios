"""Internal email string type — pure pydantic, no email-validator dependency.

Provides ``ValidEmailType`` as a drop-in replacement for ``pydantic.EmailStr``:
- Rejects missing ``@``, whitespace, domain without dots, local>64, total>254.
- Normalises the domain part to lowercase (same as EmailStr).
- Does NOT perform DNS lookups.
- Emits OpenAPI ``type=string, format=email`` for schema compatibility.

This module exists so that ``src/api/schemas/auth.py`` no longer imports
``EmailStr`` from pydantic (which transitively pulls in ``email-validator``
-> ``dnspython``), eliminating that entire dependency tree.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field
from pydantic_core import PydanticCustomError

# RFC 5321 §4.5.3: local-part max 64 octets; total max 256 (including @)
_LOCAL_MAX = 64
_TOTAL_MAX = 254

# Minimal structural regex: local@domain
# - local: any printable ASCII except space and ``<>@[]``, quoted-string not
#   supported here (we err on the side of rejection — stricter than EmailStr).
#   Includes dot (.) for common cases like user.name@domain.com.
# - domain: labels separated by dots; each label alphanumeric or hyphen, not
#   starting/ending with hyphen; TLD must have at least one dot and be alpha.
_EMAIL_RE = re.compile(
    r"^(?P<local>[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+)"
    r"@"
    r"(?P<domain>(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,})$"
)


def _validate_email(value: str) -> str:
    """Validate and normalise an email string.

    Returns the normalised string (domain lowercased).
    Raises ``PydanticCustomError`` on validation failure.
    """
    if not isinstance(value, str):
        raise PydanticCustomError(
            "email_type",
            "email must be a string",
            {},
        )

    # EmailStr rejects whitespace inside the address; we catch it early.
    if " " in value or "\t" in value or "\n" in value or "\r" in value:
        raise PydanticCustomError(
            "email_format",
            "email must not contain whitespace",
            {},
        )

    m = _EMAIL_RE.match(value)
    if m is None:
        raise PydanticCustomError(
            "email_format",
            "email is not a valid email address",
            {},
        )

    local = m.group("local")
    domain = m.group("domain")

    if len(local) > _LOCAL_MAX:
        raise PydanticCustomError(
            "email_too_long_local",
            f"email local-part exceeds {_LOCAL_MAX} characters",
            {},
        )

    total = len(local) + 1 + len(domain)  # +1 for @
    if total > _TOTAL_MAX:
        raise PydanticCustomError(
            "email_too_long",
            f"email exceeds {_TOTAL_MAX} characters total",
            {},
        )

    # Guard against a domain that is absurdly long.
    if len(domain) > _TOTAL_MAX:
        raise PydanticCustomError(
            "email_too_long",
            f"email domain exceeds {_TOTAL_MAX} characters",
            {},
        )

    # Normalise domain to lowercase (matching EmailStr behaviour).
    normalised = f"{local}@{domain.lower()}"
    return normalised


# The public type: Annotated with AfterValidator and Field for OpenAPI.
ValidEmailType = Annotated[
    str,
    AfterValidator(_validate_email),
    Field(json_schema_extra={"format": "email"}),
]
