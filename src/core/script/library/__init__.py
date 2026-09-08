"""AIOS Script library import & registry package (§9.9 DSL-16a).

`registry.py` — pure port (`LibraryRegistryPort`) + in-memory adapter,
content-hash addressed (I-04). `imports.py` — `import lib.name@version`
resolution, cycle/version-conflict rejection, and DSL-12 hash pinning.

Marketplace-backed registry adapter (HTTP/DB) is MP-3 follow-up
(DSL-16b) — out of scope here by decision (task-2373).
"""
from __future__ import annotations

from src.core.script.library.imports import (
    ImportDirective,
    ScriptLibraryCycleError,
    ScriptLibraryNotFoundError,
    compile_with_imports,
    resolve_imports,
    scan_imports,
)
from src.core.script.library.registry import (
    InMemoryLibraryRegistry,
    LibraryRef,
    LibraryRegistryPort,
    LibrarySource,
    ScriptLibraryVersionConflictError,
    body_hash,
)

__all__ = [
    "ImportDirective",
    "InMemoryLibraryRegistry",
    "LibraryRef",
    "LibraryRegistryPort",
    "LibrarySource",
    "ScriptLibraryCycleError",
    "ScriptLibraryNotFoundError",
    "ScriptLibraryVersionConflictError",
    "body_hash",
    "compile_with_imports",
    "resolve_imports",
    "scan_imports",
]
