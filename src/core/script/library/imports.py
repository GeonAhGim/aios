"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-16a —
`import lib.<name>@<version>` resolution and hash-pinning.

AIOS Script's grammar (DSL-2/3) has no `import` production, so a directive
line is recognized *before* tokenization by a fixed line pattern and, once
resolved, rewritten in place into a `#` line comment carrying the resolved
library's content hash (`registry.body_hash`). The line count is preserved
(no shift), so downstream DSL-12 error positions are unaffected, and the
comment is inert to DSL-2's lexer (`#` starts a line comment) — so the
rewritten source still compiles via `compile_source` unmodified (DSL-12
reused, not reimplemented).

Hash pinning falls out of DSL-12 `script_hash` alone: the rewritten
source *is* the `source` DSL-12 hashes, so any change to a library's body
changes its `body_hash`, which changes the comment text, which changes the
importing script's `script_hash` — no changes to `artifact/hash.py`.

Fail-closed (DoD d): resolution either fully succeeds or raises — a
cycle or missing library is never silently skipped to compile with a
partial/empty import table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.script.artifact.compile import CompiledScript, compile_source
from src.core.script.library.registry import LibraryRef, LibraryRegistryPort, LibrarySource

_IMPORT_LINE = re.compile(
    r"^(?P<indent>[ \t]*)import[ \t]+lib\.(?P<name>[A-Za-z_]\w*)@(?P<version>\S+)[ \t]*$"
)


class ScriptLibraryNotFoundError(Exception):
    """Raised when an `import` directive names an unregistered `name@version`."""

    def __init__(self, ref: LibraryRef) -> None:
        super().__init__(f"unregistered library import: {ref.key()}")
        self.ref = ref


class ScriptLibraryCycleError(Exception):
    """Raised for a circular (or self-referential) library import chain."""

    def __init__(self, cycle: list[LibraryRef]) -> None:
        path = " -> ".join(ref.key() for ref in cycle)
        super().__init__(f"circular library import: {path}")
        self.cycle = cycle


@dataclass(frozen=True)
class ImportDirective:
    line_no: int  # 1-based, matches DSL-2 Token.line
    ref: LibraryRef


def scan_imports(source: str) -> list[ImportDirective]:
    """Every `import lib.<name>@<version>` line in `source`, in file order."""
    directives = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        match = _IMPORT_LINE.match(line)
        if match is not None:
            ref = LibraryRef(name=match["name"], version=match["version"])
            directives.append(ImportDirective(line_no=line_no, ref=ref))
    return directives


def resolve_imports(
    refs: list[LibraryRef], registry: LibraryRegistryPort
) -> dict[str, LibrarySource]:
    """Recursively resolve `refs` (and their own imports) against `registry`.

    Raises `ScriptLibraryNotFoundError`/`ScriptLibraryCycleError` on the
    first failure — the returned mapping is only ever the fully-resolved
    transitive closure, never a partial one.
    """
    resolved: dict[str, LibrarySource] = {}
    stack: list[LibraryRef] = []

    def visit(ref: LibraryRef) -> None:
        if ref.key() in resolved:
            return
        if ref in stack:
            cycle = stack[stack.index(ref) :] + [ref]
            raise ScriptLibraryCycleError(cycle)
        entry = registry.get(ref)
        if entry is None:
            raise ScriptLibraryNotFoundError(ref)
        stack.append(ref)
        for directive in scan_imports(entry.source):
            visit(directive.ref)
        stack.pop()
        resolved[ref.key()] = entry

    for ref in refs:
        visit(ref)
    return resolved


def _rewrite_source(
    source: str, directives: list[ImportDirective], resolved: dict[str, LibrarySource]
) -> str:
    lines = source.splitlines()
    for directive in directives:
        entry = resolved[directive.ref.key()]
        lines[directive.line_no - 1] = (
            f"# import lib.{directive.ref.name}@{directive.ref.version}"
            f" body_hash={entry.body_hash}"
        )
    return "\n".join(lines)


def compile_with_imports(
    source: str, *, registry: LibraryRegistryPort, registry_version: str
) -> CompiledScript:
    """Resolve `source`'s `import` directives against `registry`, pin their
    hashes into the source, then compile via DSL-12 `compile_source`
    unmodified. Raises before compiling if any import is unresolved."""
    directives = scan_imports(source)
    resolved = resolve_imports([d.ref for d in directives], registry)
    rewritten = _rewrite_source(source, directives, resolved)
    return compile_source(rewritten, registry_version=registry_version)


__all__ = [
    "ImportDirective",
    "ScriptLibraryCycleError",
    "ScriptLibraryNotFoundError",
    "compile_with_imports",
    "resolve_imports",
    "scan_imports",
]
