"""CONSIST-1 배선 검사군 -- router_unregistered / port_method_unimplemented /
port_protocol_unimplemented. task-3725 CONSIST-1c로 check_consistency.py에서 분리
(순수 이동, 판정 로직 변경 없음).
"""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.consistency.common import (
    _EXCLUDE_DIR_NAMES,
    Hit,
    _callee_name,
    _is_docstring_stmt,
    _iter_py_files,
    _ratchet_allow_reason,
    _safe_parse,
)

# ---------------------------------------------------------------------------
# 1. router_unregistered
# ---------------------------------------------------------------------------


def _defines_module_router(tree: ast.Module) -> bool:
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "router" for t in node.targets
        ):
            if isinstance(node.value, ast.Call) and _callee_name(node.value.func) == "APIRouter":
                return True
    return False


def _dotted_module_name(path: Path, root: Path) -> str:
    return ".".join(path.relative_to(root).with_suffix("").parts)


def _collect_wired_router_modules(root: Path) -> set[str]:
    wired: set[str] = set()
    scan_paths: list[Path] = []
    main_py = root / "src" / "main.py"
    if main_py.exists():
        scan_paths.append(main_py)
    api_dir = root / "src" / "api"
    if api_dir.is_dir():
        scan_paths.extend(p for p in api_dir.rglob("*.py") if "__pycache__" not in p.parts)
    for path in scan_paths:
        tree = _safe_parse(path)
        if tree is None:
            continue
        alias_to_module: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    alias_to_module[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"
                and node.args
            ):
                first = node.args[0]
                if (
                    isinstance(first, ast.Attribute)
                    and first.attr == "router"
                    and isinstance(first.value, ast.Name)
                ):
                    dotted = alias_to_module.get(first.value.id)
                    if dotted:
                        wired.add(dotted)
    return wired


def check_router_wiring(root: Path) -> list[Hit]:
    routers_dir = root / "src" / "api" / "routers"
    if not routers_dir.is_dir():
        return []
    router_modules: dict[str, Path] = {}
    for path in sorted(routers_dir.rglob("*.py")):
        if path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        tree = _safe_parse(path)
        if tree is not None and _defines_module_router(tree):
            router_modules[_dotted_module_name(path, root)] = path
    if not router_modules:
        return []
    wired = _collect_wired_router_modules(root)
    return [
        (path.relative_to(root).as_posix(), 1)
        for dotted, path in sorted(router_modules.items())
        if dotted not in wired
    ]


# ---------------------------------------------------------------------------
# 2. port_method_unimplemented
# ---------------------------------------------------------------------------


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = set()
    for d in node.decorator_list:
        name = (
            _callee_name(d)
            if isinstance(d, ast.Call)
            else (
                d.id
                if isinstance(d, ast.Name)
                else (d.attr if isinstance(d, ast.Attribute) else None)
            )
        )
        if name:
            names.add(name)
    return names


def _is_raise_not_implemented_body(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = [s for s in func.body if not _is_docstring_stmt(s)]
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return False
    exc = body[0].exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    name = (
        exc.id
        if isinstance(exc, ast.Name)
        else (exc.attr if isinstance(exc, ast.Attribute) else None)
    )
    return name == "NotImplementedError"


def _abstract_methods_of(node: ast.ClassDef) -> frozenset[str]:
    return frozenset(
        item.name
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and "abstractmethod" in _decorator_names(item)
    )


def _collect_abc_ports(files: list[Path]) -> dict[str, frozenset[str]]:
    ports: dict[str, frozenset[str]] = {}
    for path in files:
        tree = _safe_parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {_callee_name(b) for b in node.bases}
            if "ABC" not in base_names:
                continue
            methods = _abstract_methods_of(node)
            if methods:
                ports[node.name] = methods
    return ports


def check_port_implementations(root: Path) -> list[Hit]:
    files = _iter_py_files(root, "src")
    ports = _collect_abc_ports(files)
    if not ports:
        return []
    hits: list[Hit] = []
    for path in files:
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        text: str | None = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {_callee_name(b) for b in node.bases}
            matched = [ports[n] for n in base_names if n in ports]
            if not matched:
                continue
            if _abstract_methods_of(node):
                continue  # 아직 abstract인 중간 계층 -- 위반 아님
            by_name = {
                item.name: item
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            for method_names in matched:
                for method_name in sorted(method_names):
                    impl = by_name.get(method_name)
                    if impl is None:
                        hits.append((rel, node.lineno))
                        continue
                    if _is_raise_not_implemented_body(impl):
                        if text is None:
                            text = path.read_text(encoding="utf-8", errors="replace")
                        if _ratchet_allow_reason(text) is None:
                            hits.append((rel, impl.lineno))
    return hits


# ---------------------------------------------------------------------------
# 2b. port_protocol_unimplemented
# ---------------------------------------------------------------------------


def _base_type_name(base: ast.expr) -> str | None:
    if isinstance(base, ast.Subscript):
        return _base_type_name(base.value)
    return _callee_name(base)


def _protocol_methods_of(node: ast.ClassDef) -> frozenset[str]:
    return frozenset(
        item.name
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and not item.name.startswith("_")
    )


def _collect_protocol_ports(files: list[Path]) -> dict[str, frozenset[str]]:
    ports: dict[str, frozenset[str]] = {}
    for path in files:
        tree = _safe_parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {_base_type_name(b) for b in node.bases}
            if "Protocol" not in base_names:
                continue
            methods = _protocol_methods_of(node)
            if methods:
                ports[node.name] = methods
    return ports


def _ports_adapters_pairs(root: Path) -> list[tuple[Path, Path]]:
    """같은 bounded context의 `ports/`-`adapters/` 형제 디렉터리 쌍만 대조한다
    (task-3724 설계: 컨텍스트 밖 클래스는 대조하지 않는다)."""
    src_dir = root / "src"
    if not src_dir.is_dir():
        return []
    pairs: list[tuple[Path, Path]] = []
    for ports_dir in sorted(src_dir.rglob("ports")):
        rel_parts = ports_dir.relative_to(root).parts
        if not ports_dir.is_dir() or _EXCLUDE_DIR_NAMES & set(rel_parts):
            continue
        adapters_dir = ports_dir.parent / "adapters"
        if adapters_dir.is_dir():
            pairs.append((ports_dir, adapters_dir))
    return pairs


_Impl = tuple[ast.FunctionDef | ast.AsyncFunctionDef, Path]
_AdapterClass = tuple[ast.ClassDef, Path, set[str]]  # (node, path, base_names)


def _own_methods_of(node: ast.ClassDef, path: Path) -> dict[str, _Impl]:
    return {
        item.name: (item, path)
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _resolve_local_methods(
    class_name: str,
    local_classes: dict[str, _AdapterClass],
    seen: set[str],
) -> dict[str, _Impl]:
    """같은 adapters/ 컨텍스트에서 로컬로 정의된 mixin 베이스까지 병합한 메서드
    집합(예: P1-D 300줄 분할로 postgres_snapshot_mixin.py에 옮겨진 메서드들,
    task-1723)을 반환한다. 자기 자신 정의가 베이스보다 우선한다."""
    if class_name in seen or class_name not in local_classes:
        return {}
    seen.add(class_name)
    node, path, base_names = local_classes[class_name]
    merged: dict[str, _Impl] = {}
    for base_name in base_names:
        merged.update(_resolve_local_methods(base_name, local_classes, seen))
    merged.update(_own_methods_of(node, path))
    return merged


def check_port_protocol_implementations(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for ports_dir, adapters_dir in _ports_adapters_pairs(root):
        port_files = [p for p in sorted(ports_dir.rglob("*.py")) if p.name != "__init__.py"]
        protocols = _collect_protocol_ports(port_files)
        if not protocols:
            continue
        # 긴 이름부터 매칭해 접미사 부분 충돌(예: "...Source" vs "...MarkPriceSource")을 피한다.
        protocol_names = sorted(protocols, key=len, reverse=True)
        adapter_files = [
            p for p in sorted(adapters_dir.rglob("*.py")) if p.name != "__init__.py"
        ]
        adapter_texts: dict[Path, str] = {}
        local_classes: dict[str, _AdapterClass] = {}
        adapter_trees: dict[Path, ast.Module] = {}
        for path in adapter_files:
            tree = _safe_parse(path)
            if tree is None:
                continue
            adapter_trees[path] = tree
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                base_names: set[str] = {
                    name for b in node.bases if (name := _base_type_name(b)) is not None
                }
                local_classes.setdefault(node.name, (node, path, base_names))
        for path, tree in adapter_trees.items():
            rel = path.relative_to(root).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                matched_protocol = next(
                    (name for name in protocol_names if node.name.endswith(name)),
                    None,
                )
                if matched_protocol is None:
                    continue
                by_name = _resolve_local_methods(node.name, local_classes, set())
                for method_name in sorted(protocols[matched_protocol]):
                    found = by_name.get(method_name)
                    if found is None:
                        hits.append((rel, node.lineno))
                        continue
                    impl, impl_path = found
                    if _is_raise_not_implemented_body(impl):
                        if impl_path not in adapter_texts:
                            adapter_texts[impl_path] = impl_path.read_text(
                                encoding="utf-8", errors="replace"
                            )
                        if _ratchet_allow_reason(adapter_texts[impl_path]) is None:
                            hits.append((rel, impl.lineno))
    return hits
