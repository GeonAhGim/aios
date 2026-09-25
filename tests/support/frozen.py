"""Helpers for asserting that frozen models and dataclasses reject mutation."""

from __future__ import annotations

from typing import Any


def assign_attr(target: Any, name: str, value: object) -> None:
    """Assign `target.<name> = value` through the normal attribute protocol.

    Frozen dataclasses and frozen pydantic models raise on assignment. Routing the
    write through `setattr` with a runtime attribute name keeps the call site
    type-clean (a literal `obj.field = ...` on a frozen type is a static error)
    while exercising exactly the same rejection path.
    """
    setattr(target, name, value)
