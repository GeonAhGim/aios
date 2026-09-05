"""R-56 적대적 — I9: risk 판단 경로는 LLM 클라이언트·프롬프트 서비스에 닿지 않는다.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §4.1 I9("import graph 검사:
anthropic, openai, google.generativeai, src/services/strategy_prompt_service,
src/core/llm"), §8 적대적 "LLM import 그래프 0건", §9 R-56.
docs/design/INVARIANTS.md I-10("구현됨 ≠ 작동함" — 정적 검사로 증명).

세 층으로 증명한다:
1. AST **추이적** import 그래프 — 보호 경로(I9 6곳 + risk_gate 사전검사
   경로 `order_service/{gate,foundation_gate,fenced_submit}.py`,
   `execution_loop/tick_risk_phase.py`, `risk_guard_service.py`)에서 출발해
   `src.*` import를 따라가며 금지 모듈에 닿는 경로가 0건임을 단언한다.
   직접 import만 보면 "깨끗한 중간 모듈"을 거쳐 우회할 수 있으므로 반드시
   추이적으로 본다. `importlib.import_module("anthropic")`/`__import__`
   문자열 리터럴도 import로 취급한다(동적 import로 AST를 피하는 경로 차단).
2. 런타임 — 보호 모듈 전부를 자식 프로세스에서 실제로 import한 뒤
   `sys.modules`에 금지 루트가 없음을 단언한다(AST가 못 보는 경로의 2차 방어).
3. 위조 주입 negative — 임시 트리에 우회 샘플(중간 모듈 경유·동적 import·
   TYPE_CHECKING 블록)을 심어 체커가 반드시 잡는지 확인한다. 체커가 위조를
   놓치면 이 테스트 자체가 실패한다.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

GUARDED_PATHS: tuple[str, ...] = (
    "src/core/risk",
    "src/core/risk_stats",
    "src/core/safety",
    "src/foundation/risk_gate",
    "src/services/safety",
    "src/services/risk_decision_recorder.py",
    "src/services/order_service/gate.py",
    "src/services/order_service/foundation_gate.py",
    "src/services/order_service/fenced_submit.py",
    "src/services/execution_loop/tick_risk_phase.py",
    "src/services/risk_guard_service.py",
)

BANNED_MODULES: frozenset[str] = frozenset(
    {
        "anthropic",
        "openai",
        "google.generativeai",
        "google.genai",
        "litellm",
        "langchain",
        "langchain_core",
        "langchain_anthropic",
        "langchain_openai",
        "src.core.llm",
        "src.services.llm",
        "src.services.strategy_prompt_service",
    }
)
_DYNAMIC_IMPORT_CALLS = frozenset({"__import__", "import_module"})

Violation = tuple[str, str, tuple[str, ...]]  # (file, banned module, import chain)


def matches_banned(module: str, banned: frozenset[str]) -> str | None:
    for name in banned:
        if module == name or module.startswith(name + "."):
            return name
    return None


def _imported_names(tree: ast.AST) -> list[str]:
    """정적 import + 동적 import 문자열 리터럴. `from a.b import c`는 `c`가
    서브모듈일 수 있으므로 `a.b`와 `a.b.c` 둘 다 후보로 낸다."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if callee in _DYNAMIC_IMPORT_CALLS and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.append(first.value)
    return names


def module_to_path(module: str, root: Path) -> Path | None:
    """`src.a.b` → 가장 긴 접두어가 가리키는 파일(`b.py` 또는 `b/__init__.py`)."""
    parts = module.split(".")
    while parts:
        candidate = root.joinpath(*parts)
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            return candidate / "__init__.py"
        if candidate.with_suffix(".py").exists():
            return candidate.with_suffix(".py")
        parts.pop()
    return None


def _expand(root: Path, guarded: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for rel in guarded:
        path = root / rel
        files.extend([path] if path.is_file() else sorted(path.rglob("*.py")))
    return files


def scan_import_graph(
    root: Path, guarded: tuple[str, ...], banned: frozenset[str]
) -> tuple[list[Violation], set[Path]]:
    """BFS로 `src.*` 의존을 따라가며 금지 모듈 도달을 수집한다. 순수 함수(파일
    읽기만). 반환: (위반 목록, 도달한 파일 집합)."""
    start = _expand(root, guarded)
    parent: dict[Path, Path | None] = {path: None for path in start}
    queue = deque(start)
    violations: list[Violation] = []

    def chain(path: Path) -> tuple[str, ...]:
        links: list[str] = []
        cursor: Path | None = path
        while cursor is not None:
            links.append(cursor.relative_to(root).as_posix())
            cursor = parent[cursor]
        return tuple(reversed(links))

    while queue:
        path = queue.popleft()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _imported_names(tree):
            hit = matches_banned(module, banned)
            if hit is not None:
                violations.append((path.relative_to(root).as_posix(), module, chain(path)))
                continue
            if not module.startswith("src."):
                continue
            target = module_to_path(module, root)
            if target is not None and target not in parent:
                parent[target] = path
                queue.append(target)
    return violations, set(parent)


def _format(violations: list[Violation]) -> str:
    return "\n".join(
        f"{file} imports {module} via {' -> '.join(chain)}" for file, module, chain in violations
    )


# --- 1. 실제 리포 그래프 ---------------------------------------------------


def test_guarded_paths_exist() -> None:
    """보호 대상이 사라지면 아래 검사는 아무것도 지키지 못한 채 통과한다."""
    missing = [rel for rel in GUARDED_PATHS if not (ROOT / rel).exists()]
    assert not missing, f"I9 보호 경로가 없음(스펙 §4.1 I9 갱신 필요): {missing}"


def test_rsk_i9_risk_path_reaches_no_llm_module_transitively() -> None:
    violations, reached = scan_import_graph(ROOT, GUARDED_PATHS, BANNED_MODULES)
    assert not violations, "I9 위반 — risk 경로에서 LLM/프롬프트 모듈 도달:\n" + _format(violations)
    # 추이 탐색이 실제로 간선을 따라갔는지(출발 파일만 보고 끝나지 않았는지) 확인.
    assert len(reached) > len(_expand(ROOT, GUARDED_PATHS))
    assert ROOT / "src/core/risk/engine.py" in reached
    assert ROOT / "src/services/order_service/fenced_submit.py" in reached


# --- 2. 런타임 import ------------------------------------------------------

_RUNTIME_PROBE = """
import importlib, json, sys
modules, banned = json.loads(sys.argv[1]), json.loads(sys.argv[2])
for name in modules:
    importlib.import_module(name)
loaded = sorted(
    key for key in sys.modules
    if any(key == b or key.startswith(b + ".") for b in banned)
)
print(json.dumps(loaded))
"""


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def test_rsk_i9_runtime_import_of_guarded_modules_loads_no_llm_client() -> None:
    modules = sorted({_module_name(path) for path in _expand(ROOT, GUARDED_PATHS)})
    banned = json.dumps(sorted(BANNED_MODULES))
    result = subprocess.run(
        [sys.executable, "-c", _RUNTIME_PROBE, json.dumps(modules), banned],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, f"보호 모듈 import 실패:\n{result.stderr[-2000:]}"
    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    assert loaded == [], f"런타임에 LLM 모듈이 적재됨(I9 위반): {loaded}"


# --- 3. 위조 주입 negative -------------------------------------------------

_CLEAN_ROOT = "from src.services.helper import compute\n"
_INJECTIONS: dict[str, str] = {
    "direct_import": "import anthropic\n",
    "from_import": "from openai import OpenAI\n",
    "submodule_import": "import google.generativeai as genai\n",
    "prompt_service": "from src.services.strategy_prompt_service import build_prompt\n",
    "internal_llm_pkg": "from src.core.llm.client import Client\n",
    "dynamic_import_module": "import importlib\nclient = importlib.import_module('anthropic')\n",
    "dynamic_dunder_import": "mod = __import__('litellm')\n",
    "type_checking_block": (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from anthropic import Anthropic\n"
    ),
    "function_local_import": "def call():\n    from openai import OpenAI\n    return OpenAI\n",
}


def _fake_tree(tmp_path: Path, helper_source: str) -> Path:
    """보호 파일은 깨끗하고, 그것이 import하는 중간 모듈이 금지 모듈에 닿는 트리."""
    (tmp_path / "src/core/risk").mkdir(parents=True)
    (tmp_path / "src/services").mkdir(parents=True)
    for pkg in ("src", "src/core", "src/core/risk", "src/services"):
        (tmp_path / pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src/core/risk/engine.py").write_text(_CLEAN_ROOT, encoding="utf-8")
    (tmp_path / "src/services/helper.py").write_text(
        helper_source + "\ndef compute():\n    return 1\n", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.parametrize("injection", sorted(_INJECTIONS))
def test_negative_injected_llm_import_is_detected_through_intermediate_module(
    tmp_path: Path, injection: str
) -> None:
    root = _fake_tree(tmp_path, _INJECTIONS[injection])
    violations, _ = scan_import_graph(root, ("src/core/risk",), BANNED_MODULES)
    assert violations, f"체커 결함 — 위조 주입({injection})을 놓침"
    file, _module, chain = violations[0]
    assert file == "src/services/helper.py"
    assert chain == ("src/core/risk/engine.py", "src/services/helper.py")


def test_negative_control_clean_tree_has_no_violation(tmp_path: Path) -> None:
    root = _fake_tree(tmp_path, "import decimal\n")
    violations, reached = scan_import_graph(root, ("src/core/risk",), BANNED_MODULES)
    assert violations == []
    assert root / "src/services/helper.py" in reached  # 추이 탐색은 실제로 일어났다
