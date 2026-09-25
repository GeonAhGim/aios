"""Immutable dict/list wrappers for I-09 nested-container freezing.

`frozen=True` on a pydantic model only blocks reassigning a field itself
(`model.evidence = {}`); the dict/list object the field points at is still
an ordinary mutable container, so `model.evidence.clear()` or
`model.rule_hits.append(...)` tampers content in place without raising
(I-09 violation, task-4937 REJECT of task-4916). A `field_validator` wraps
the incoming value in one of these subclasses at construction time so every
mutating call raises instead — while the declared field type stays
`dict[str, Any]` / `list[...]` (no public contract-type change, P5 guard).
"""

from __future__ import annotations

from typing import Any, TypeVar

K = TypeVar("K")
V = TypeVar("V")
T = TypeVar("T")


class FrozenDict(dict[K, V]):
    """`dict` subclass that raises on every mutating call."""

    def _blocked(self, *_args: object, **_kwargs: object) -> Any:
        raise TypeError("this dict is frozen (I-09) — mutation is not allowed")

    __setitem__ = _blocked
    __delitem__ = _blocked
    __ior__ = _blocked
    clear = _blocked
    pop = _blocked
    popitem = _blocked
    setdefault = _blocked
    update = _blocked

    def __reduce__(self) -> tuple[Any, ...]:
        # Default dict pickling round-trips through `update`/`__setitem__`,
        # which are blocked above — reconstruct via the constructor instead
        # (pickling crosses a process boundary, e.g. ProcessPoolExecutor).
        return (self.__class__, (dict(self),))


class FrozenList(list[T]):
    """`list` subclass that raises on every mutating call — see `FrozenDict`."""

    def _blocked(self, *_args: object, **_kwargs: object) -> Any:
        raise TypeError("this list is frozen (I-09) — mutation is not allowed")

    __setitem__ = _blocked
    __delitem__ = _blocked
    __iadd__ = _blocked
    __imul__ = _blocked
    append = _blocked
    extend = _blocked
    insert = _blocked
    remove = _blocked
    pop = _blocked
    clear = _blocked
    sort = _blocked
    reverse = _blocked

    def __reduce__(self) -> tuple[Any, ...]:
        # Default list pickling round-trips through `extend`/`append`,
        # which are blocked above — reconstruct via the constructor instead
        # (pickling crosses a process boundary, e.g. ProcessPoolExecutor).
        return (self.__class__, (list(self),))
