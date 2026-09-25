"""DB role separation SQL generator — owner (migrator) vs application (app).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §2.1 L0-3, §9 L0-3

`aios_migrator` is the table owner role that runs migrations; `aios_app` is
the DML-only role the runtime application connects with. Actual WORM
enforcement for append-only tables is handled by the trigger in
[[append_only.worm_sql]] — this module fulfills the prerequisite
(9ec8a1ee28d7 docstring) that the application must connect under a separate
non-owner role for that trigger to take effect.

PostgreSQL does not support `CREATE ROLE IF NOT EXISTS`, so we check
existence inside a `DO` block. This module only generates SQL strings —
execution is delegated to L0-5.
"""
from __future__ import annotations

import re

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class InvalidIdentifierError(ValueError):
    """Role name is not a safe SQL identifier format or overlaps (injection prevention)."""


def _validate_role(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise InvalidIdentifierError(f"Role name is not a safe identifier: {name!r}")


def _ensure_role_sql(role: str, *, login: bool) -> str:
    option = "LOGIN" if login else "NOLOGIN"
    return (
        "DO $$\n"
        "BEGIN\n"
        f"    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{role}') THEN\n"
        f"        CREATE ROLE {role} {option};\n"
        "    END IF;\n"
        "END\n"
        "$$"
    )


def ensure_roles_sql(app_role: str, migrator_role: str) -> list[str]:
    """SQL list to create `migrator_role` (owner) and `app_role` (DML-only) + grant base privileges.

    `app_role` receives SELECT/INSERT/UPDATE/DELETE on tables within the schema —
    blocking UPDATE/DELETE on append-only tables is handled separately by the
    trigger and REVOKE in `worm_sql` at the table level (this function only sets
    schema-wide defaults).
    """
    _validate_role(app_role)
    _validate_role(migrator_role)
    if app_role == migrator_role:
        raise InvalidIdentifierError("app_role and migrator_role must not be the same")
    return [
        _ensure_role_sql(migrator_role, login=True),
        _ensure_role_sql(app_role, login=True),
        f"GRANT ALL PRIVILEGES ON SCHEMA public TO {migrator_role}",
        f"GRANT USAGE ON SCHEMA public TO {app_role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}",
        (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator_role} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_role}"
        ),
        (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator_role} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {app_role}"
        ),
    ]
