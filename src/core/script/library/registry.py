"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-16a —
library registry: a pure port (Protocol) plus an in-memory adapter.

Scope (task-2373 decision): import resolution and hash-pinning only. The
marketplace-backed adapter (HTTP/DB) is MP-3 follow-up (DSL-16b) — this
module never performs I/O.

I-04 (content-hash addressing, immutability): once `name@version` is
registered, its body is fixed. Re-registering the same key with a
different body is rejected and the stored body is left untouched
(fail-closed) — only a byte-identical re-register is a no-op.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LibraryRef:
    """`lib.<name>@<version>` reference, e.g. `import lib.foo@1`."""

    name: str
    version: str

    def key(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class LibrarySource:
    """A registered library body plus its content hash (sha256 of `source`)."""

    ref: LibraryRef
    source: str
    body_hash: str


class ScriptLibraryVersionConflictError(Exception):
    """Raised when `name@version` is already registered with a different body."""

    def __init__(self, ref: LibraryRef) -> None:
        super().__init__(
            f"library version conflict for {ref.key()}: registered body hash differs"
        )
        self.ref = ref


def body_hash(source: str) -> str:
    """sha256 hex of the raw library source bytes. Not DSL-12 `script_hash` —
    libraries are not compiled standalone here, only content-addressed."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class LibraryRegistryPort(Protocol):
    """Pure port DSL-16a depends on. No HTTP/DB calls (MP-3 is out of scope)."""

    def get(self, ref: LibraryRef) -> LibrarySource | None: ...

    def register(self, ref: LibraryRef, source: str) -> LibrarySource: ...


class InMemoryLibraryRegistry:
    """In-memory `LibraryRegistryPort` implementation for tests and DSL-16a."""

    def __init__(self) -> None:
        self._entries: dict[str, LibrarySource] = {}

    def get(self, ref: LibraryRef) -> LibrarySource | None:
        return self._entries.get(ref.key())

    def register(self, ref: LibraryRef, source: str) -> LibrarySource:
        digest = body_hash(source)
        existing = self._entries.get(ref.key())
        if existing is not None:
            if existing.body_hash != digest:
                raise ScriptLibraryVersionConflictError(ref)
            return existing
        entry = LibrarySource(ref=ref, source=source, body_hash=digest)
        self._entries[ref.key()] = entry
        return entry


__all__ = [
    "InMemoryLibraryRegistry",
    "LibraryRef",
    "LibraryRegistryPort",
    "LibrarySource",
    "ScriptLibraryVersionConflictError",
    "body_hash",
]
