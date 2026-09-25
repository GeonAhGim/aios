"""U-3a 적대적 테스트 -- AI 어시스턴트 경로는 구조적으로 주문 실행에 접근할
수 없다(임포트 그래프 정적 검사).

Spec DoD: "실행은 절대 자동으로 하지 않는다(주문 경로 접근 금지)". 진짜
안전 경계는 "이 모듈 트리 어디에도 실행 경로를 임포트하는 파일이 없다"는
사실이다 -- 아무리 프롬프트 인젝션으로 provider가 악성 스크립트를 생성해도,
이 트리에는 그 스크립트를 실행할 함수 자체가 없다(컴파일까지만 한다).
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_MODULE_PREFIXES = (
    "src.core.executor",
    "src.services.execution_loop",
    "src.exchanges",
    "src.foundation.execution_ownership",
    "src.foundation.ems",
    "src.foundation.oms",
    "src.foundation.order_service",
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
ASSISTANT_ROOTS = (
    _REPO_ROOT / "src" / "foundation" / "ai" / "assistant",
    _REPO_ROOT / "src" / "api" / "routers" / "assistant.py",
)


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for root in ASSISTANT_ROOTS:
        if root.is_dir():
            files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
        elif root.is_file():
            files.append(root)
    return files


def _imported_module_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_assistant_tree_never_imports_execution_paths() -> None:
    files = _iter_py_files()
    assert files, "assistant 소스 트리를 찾지 못함 -- 경로 점검 필요"

    violations: list[tuple[str, str]] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module_name in _imported_module_names(tree):
            if any(
                module_name == prefix or module_name.startswith(prefix + ".")
                for prefix in FORBIDDEN_MODULE_PREFIXES
            ):
                violations.append((str(path), module_name))

    assert violations == []
