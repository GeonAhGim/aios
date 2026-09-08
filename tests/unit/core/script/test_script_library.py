"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-16a —
`library/{imports,registry}.py` 테스트.

DoD 5개를 각각 단언한다: (a) 순환·자기참조 거부, (b) 버전(본문 해시)
불일치 거부 + 등록본 불변, (c) 라이브러리 본문 1바이트 변경이 상위
스크립트의 DSL-12 `script_hash`를 바꾼다(리프 핵심 단언), (d) 미등록
라이브러리 import는 fail-closed, (e)는 코드 리뷰 대상(HTTP/DB 없음)이라
여기서는 별도 단언하지 않는다.
"""
from __future__ import annotations

import pytest

from src.core.script.artifact import ScriptCompileError
from src.core.script.library import (
    InMemoryLibraryRegistry,
    LibraryRef,
    ScriptLibraryCycleError,
    ScriptLibraryNotFoundError,
    ScriptLibraryVersionConflictError,
    compile_with_imports,
    resolve_imports,
    scan_imports,
)

REG = "r" * 64
PARENT = (
    "import lib.util@1\n"
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "let prev = close[1]\n"
    "signal go_long = rsi_val < 30 and close > prev\n"
    "plot(rsi_val, 1)\n"
    "order(buy, 1, 2) when go_long"
)


# ---- scan/resolve ----


def test_scan_imports_finds_directives_in_file_order() -> None:
    source = "import lib.a@1\ninput x: int = 1\nimport lib.b@2\n"
    directives = scan_imports(source)
    assert [(d.line_no, d.ref.key()) for d in directives] == [(1, "a@1"), (3, "b@2")]


def test_resolve_imports_returns_transitive_closure() -> None:
    registry = InMemoryLibraryRegistry()
    registry.register(LibraryRef("base", "1"), "let x = 1\n")
    registry.register(LibraryRef("util", "1"), "import lib.base@1\nlet y = 2\n")
    resolved = resolve_imports([LibraryRef("util", "1")], registry)
    assert set(resolved) == {"util@1", "base@1"}


# ---- (a) 순환/자기참조 거부 ----


def test_resolve_imports_self_reference_is_rejected() -> None:
    registry = InMemoryLibraryRegistry()
    registry.register(LibraryRef("a", "1"), "import lib.a@1\n")
    with pytest.raises(ScriptLibraryCycleError) as info:
        resolve_imports([LibraryRef("a", "1")], registry)
    assert [r.key() for r in info.value.cycle] == ["a@1", "a@1"]


def test_resolve_imports_mutual_cycle_is_rejected() -> None:
    registry = InMemoryLibraryRegistry()
    registry.register(LibraryRef("a", "1"), "import lib.b@1\n")
    registry.register(LibraryRef("b", "1"), "import lib.a@1\n")
    with pytest.raises(ScriptLibraryCycleError) as info:
        resolve_imports([LibraryRef("a", "1")], registry)
    assert info.value.cycle[0].key() == info.value.cycle[-1].key() == "a@1"


# ---- (d) 미등록 라이브러리 = fail-closed ----


def test_resolve_imports_missing_library_raises_not_found() -> None:
    registry = InMemoryLibraryRegistry()
    with pytest.raises(ScriptLibraryNotFoundError) as info:
        resolve_imports([LibraryRef("ghost", "1")], registry)
    assert info.value.ref.key() == "ghost@1"


def test_compile_with_imports_unregistered_import_never_compiles() -> None:
    registry = InMemoryLibraryRegistry()
    with pytest.raises(ScriptLibraryNotFoundError):
        compile_with_imports(PARENT, registry=registry, registry_version=REG)


# ---- (b) 버전(본문 해시) 불일치 거부 + 등록본 불변 ----


def test_register_rejects_different_body_same_key_and_keeps_original() -> None:
    registry = InMemoryLibraryRegistry()
    ref = LibraryRef("util", "1")
    registry.register(ref, "let a = 1\n")
    with pytest.raises(ScriptLibraryVersionConflictError):
        registry.register(ref, "let a = 2\n")
    stored = registry.get(ref)
    assert stored is not None
    assert stored.source == "let a = 1\n"


def test_register_same_body_is_idempotent() -> None:
    registry = InMemoryLibraryRegistry()
    ref = LibraryRef("util", "1")
    first = registry.register(ref, "let a = 1\n")
    second = registry.register(ref, "let a = 1\n")
    assert first == second


# ---- (c) 해시 고정 관통 (핵심 단언) ----


def test_compile_with_imports_hash_changes_when_library_body_changes() -> None:
    registry_v1 = InMemoryLibraryRegistry()
    registry_v1.register(LibraryRef("util", "1"), "let helper = 1\n")
    compiled_v1 = compile_with_imports(PARENT, registry=registry_v1, registry_version=REG)

    registry_v2 = InMemoryLibraryRegistry()
    registry_v2.register(LibraryRef("util", "1"), "let helper = 2\n")  # 1바이트 변경
    compiled_v2 = compile_with_imports(PARENT, registry=registry_v2, registry_version=REG)

    assert compiled_v1.script_hash != compiled_v2.script_hash
    assert compiled_v1.ir_bytes == compiled_v2.ir_bytes  # IR은 그대로 — 해시만 소스로 갈린다


def test_compile_with_imports_is_deterministic_for_same_registry_state() -> None:
    registry = InMemoryLibraryRegistry()
    registry.register(LibraryRef("util", "1"), "let helper = 1\n")
    a = compile_with_imports(PARENT, registry=registry, registry_version=REG)
    b = compile_with_imports(PARENT, registry=registry, registry_version=REG)
    assert a.script_hash == b.script_hash


def test_import_directive_rewrite_preserves_line_numbers_for_errors() -> None:
    registry = InMemoryLibraryRegistry()
    registry.register(LibraryRef("util", "1"), "let a = 1\n")
    source = "import lib.util@1\nlet bad = zz\n"
    with pytest.raises(ScriptCompileError) as info:
        compile_with_imports(source, registry=registry, registry_version=REG)
    assert (info.value.line, info.value.col) == (2, 1)
