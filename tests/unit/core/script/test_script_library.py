"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-16a —
`library/{imports,registry}.py` 테스트.

DoD 5개를 각각 단언한다: (a) 순환·자기참조 거부, (b) 버전(본문 해시)
불일치 거부 + 등록본 불변, (c) 라이브러리 본문 1바이트 변경이 상위
스크립트의 DSL-12 `script_hash`를 바꾼다(리프 핵심 단언), (d) 미등록
라이브러리 import는 fail-closed, (e)는 코드 리뷰 대상(HTTP/DB 없음)이라
여기서는 별도 단언하지 않는다.

DEEPEN(task-2935, 원 task-2373 DEPTH 감사 부족분): negative는 이미
6건으로 충분하다고 판단하고 추가하지 않는다. 대신 (1) 실패 주입 1건
(registry.get이 커넥션 오류를 던지는 상황을 흉내내 fail-closed 전파를
확인), (2) 수치 성능 단언 1건(다이아몬드형 import 그래프에서 `resolved`
캐시가 방문 횟수를 O(depth)로 묶는지 -- import 해석 지연 방지), (3)
게이트 적색 재현 1건(순환 import 가드가 없는 순진한 재귀는 실제로
RecursionError로 레드가 됨을 먼저 재현하고, 실장 코드는 그 가드 덕에
깔끔한 예외로 fail-closed함을 대조)을 추가한다.
"""

from __future__ import annotations

import time

import pytest

from src.core.script.artifact import ScriptCompileError
from src.core.script.library import (
    InMemoryLibraryRegistry,
    LibraryRef,
    LibrarySource,
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


# ---- DEEPEN(task-2935): 실패 주입 ----


class _FlakyRegistry:
    """실패 주입용 래퍼 -- 지정한 `name@version`을 조회할 때 (향후 MP-3
    HTTP/DB 어댑터가 겪을 법한) 커넥션 오류를 던진다. `LibraryRegistryPort`를
    구조적으로 만족한다(registry.py의 Protocol에 등록할 필요 없음)."""

    def __init__(self, inner: InMemoryLibraryRegistry, fail_on_key: str) -> None:
        self._inner = inner
        self._fail_on_key = fail_on_key

    def get(self, ref: LibraryRef) -> LibrarySource | None:
        if ref.key() == self._fail_on_key:
            raise ConnectionError(f"injected failure for {ref.key()}")
        return self._inner.get(ref)

    def register(self, ref: LibraryRef, source: str) -> LibrarySource:
        return self._inner.register(ref, source)


class _CountingRegistry:
    """`registry.get` 호출 횟수를 세는 래퍼 (수치 성능 단언용)."""

    def __init__(self, inner: InMemoryLibraryRegistry) -> None:
        self._inner = inner
        self.get_calls = 0

    def get(self, ref: LibraryRef) -> LibrarySource | None:
        self.get_calls += 1
        return self._inner.get(ref)

    def register(self, ref: LibraryRef, source: str) -> LibrarySource:
        return self._inner.register(ref, source)


def test_compile_with_imports_propagates_injected_registry_failure_fail_closed() -> None:
    """registry.get이 예외를 던지면 compile_with_imports는 그것을 그대로
    전파해야 한다 -- 잡아 삼키거나 빈/부분 CompiledScript로 대체하면 모듈
    docstring의 fail-closed 계약(부분 import 테이블로 컴파일 금지) 위반."""
    inner = InMemoryLibraryRegistry()
    inner.register(LibraryRef("util", "1"), "let helper = 1\n")
    flaky = _FlakyRegistry(inner, fail_on_key="util@1")
    with pytest.raises(ConnectionError, match="injected failure for util@1"):
        compile_with_imports(PARENT, registry=flaky, registry_version=REG)


# ---- DEEPEN(task-2935): 수치 성능 단언(import 해석 지연) ----


def test_resolve_imports_diamond_graph_visits_each_library_once() -> None:
    """`resolved` 캐시(imports.py: `if ref.key() in resolved: return`)가
    없다면 다이아몬드형(공유 의존) import 그래프의 방문 횟수는 depth에
    지수적으로(O(2**depth)) 폭발한다. depth=20이면 캐시 없이는 2**20(~100만)
    회 이상 재귀해야 한다. 이 테스트는 실제 registry.get 호출 횟수가 총
    고유 라이브러리 수를 넘지 않고(O(depth)), 벽시계 시간도 짧은 상한
    안에 끝남을 단언해 캐시 제거 회귀를 성능 저하로 즉시 드러낸다."""
    registry = InMemoryLibraryRegistry()
    registry.register(LibraryRef("base", "1"), "let x = 0\n")
    depth = 20
    prev = [LibraryRef("base", "1")]
    for level in range(1, depth + 1):
        current = []
        for branch in ("a", "b"):
            body = "".join(f"import lib.{r.name}@{r.version}\n" for r in prev) + "let y = 1\n"
            ref = LibraryRef(f"d{level}_{branch}", "1")
            registry.register(ref, body)
            current.append(ref)
        prev = current

    counting = _CountingRegistry(registry)

    total_unique_libs = 1 + depth * 2  # base + 2개/레벨
    start = time.perf_counter()
    resolved = resolve_imports(prev, counting)
    elapsed = time.perf_counter() - start

    assert len(resolved) == total_unique_libs
    assert counting.get_calls == total_unique_libs  # 각 라이브러리는 정확히 한 번만 조회된다
    assert elapsed < 1.0  # 캐시 없이 지수 재귀하면 초 단위를 훨씬 초과한다


# ---- DEEPEN(task-2935): 게이트 적색 재현(순환 import 회귀 가드) ----


def test_resolve_imports_cycle_guard_prevents_recursion_error_regression() -> None:
    """resolve_imports의 `if ref in stack: raise ScriptLibraryCycleError`
    가드(imports.py)가 빠진 순진한 재귀 구현이 순환 import에서 실제로 어떻게
    레드(RecursionError로 뻗음)가 되는지 먼저 재현한 뒤, 실장 코드는 동일
    입력에서 그 가드 덕분에 깔끔한 예외로 fail-closed함을 대조 단언한다.
    이 가드가 삭제/약화되는 회귀가 나면 두 번째 단언이 RecursionError로
    바뀌며 이 테스트가 레드가 된다."""
    registry = InMemoryLibraryRegistry()
    registry.register(LibraryRef("a", "1"), "import lib.b@1\n")
    registry.register(LibraryRef("b", "1"), "import lib.a@1\n")

    def naive_resolve_without_cycle_guard(ref: LibraryRef) -> None:
        entry = registry.get(ref)
        assert entry is not None
        for directive in scan_imports(entry.source):
            naive_resolve_without_cycle_guard(directive.ref)  # 방문 기록 없음

    with pytest.raises(RecursionError):
        naive_resolve_without_cycle_guard(LibraryRef("a", "1"))

    with pytest.raises(ScriptLibraryCycleError):
        resolve_imports([LibraryRef("a", "1")], registry)
