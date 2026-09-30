"""scripts/consistency/wiring.py perf/구조 회귀 가드 -- task-9122.

task-8000 -> task-8949 -> task-9011로 esc-ci-consistency의 120s 타임아웃이
세 차례 반복 발행됐다(같은 패턴: 어떤 검사 함수가 이미 캐시된 파일을 다시
읽거나 이미 파싱된 tree를 다시 `ast.walk`한다). 매번 사후에(타임아웃이 실제로
발생한 뒤) 발견됐다 -- 지금까지는 회귀를 구조적으로 막는 테스트가 없었다.

`check_port_protocol_implementations`(scripts/consistency/wiring.py)에 그
패턴이 하나 더 남아 있었다: adapter 파일마다 `ast.walk(tree)`를 두 번(로컬
클래스 색인용 1회 + protocol 매칭용 1회) 실행했다. task-9122로 한 번만
walk하도록 고치고, 아래 테스트로 그 함수가 다시 두 번 걷지 않는지를
구조적으로 고정한다(파일 개수가 적어 겉보기엔 무해해도, 이 저장소의
반복 사고 이력상 "작아 보이는 중복 walk"가 쌓여 CI를 죽인 전례가 있다).
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
WIRING_PATH = SCRIPTS_DIR / "consistency" / "wiring.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_module("check_consistency_perf", SCRIPTS_DIR / "check_consistency.py")


def _write(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


_PROTOCOL_PORT_SRC = (
    "from typing import Protocol\n\n"
    "class WidgetRepository(Protocol):\n"
    "    async def get(self) -> None: ...\n"
    "    async def save(self) -> None: ...\n"
)


def _ast_walk_call_count(func: ast.FunctionDef) -> int:
    return sum(
        1
        for n in ast.walk(func)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "walk"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "ast"
    )


def test_port_protocol_implementations_walks_each_adapter_tree_once() -> None:
    """구조 가드: task-8949/task-9011과 같은 이중 `ast.walk(tree)` 재발을
    CI 타임아웃이 아니라 이 테스트가 즉시 잡는다."""
    tree = ast.parse(WIRING_PATH.read_text(encoding="utf-8"))
    func = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "check_port_protocol_implementations"
    )
    walk_calls = _ast_walk_call_count(func)
    assert walk_calls == 1, (
        f"check_port_protocol_implementations has {walk_calls} ast.walk(...) call "
        "sites, expected 1 -- reuse the ClassDef list captured on the first pass "
        "instead of re-walking the same adapter tree a second time"
    )


def test_port_protocol_implementations_skips_unparseable_adapter_file(tmp_path: Path) -> None:
    """failure-injection: 문법 오류가 있는 adapter 파일 하나가 있어도
    나머지 파일은 정상 검사된다(파일 하나 파싱 실패로 전체가 죽지 않는다)."""
    _write(tmp_path, "src/ctx/ports/repository.py", _PROTOCOL_PORT_SRC)
    _write(tmp_path, "src/ctx/adapters/broken.py", "class Broken(:\n")
    _write(
        tmp_path,
        "src/ctx/adapters/postgres_repository.py",
        "class PostgresWidgetRepository:\n    async def get(self) -> None:\n        return None\n",
    )
    hits = cc.check_port_protocol_implementations(tmp_path)
    assert hits == [("src/ctx/adapters/postgres_repository.py", 1)]


def test_port_protocol_implementations_perf_budget(tmp_path: Path) -> None:
    """numeric perf assertion: 60 adapter 파일 x 20 클래스(1,200 클래스,
    구현 완전)를 5초 안에 처리한다 -- esc-ci-consistency의 120s 예산에
    비해 넉넉한 여유를 두면서도, 파일당 이중 walk가 재발하면 이 크기에서도
    체감될 만큼 느려지도록 구성했다."""
    _write(tmp_path, "src/ctx/ports/repository.py", _PROTOCOL_PORT_SRC)
    for i in range(60):
        body = "\n".join(
            f"class Adapter{i}_{j}WidgetRepository:\n"
            "    async def get(self) -> None:\n        return None\n"
            "    async def save(self) -> None:\n        return None\n"
            for j in range(20)
        )
        _write(tmp_path, f"src/ctx/adapters/mod_{i}.py", body)

    start = time.perf_counter()
    hits = cc.check_port_protocol_implementations(tmp_path)
    elapsed = time.perf_counter() - start

    assert hits == []
    assert elapsed < 5.0, (
        f"check_port_protocol_implementations took {elapsed:.2f}s for 1,200 "
        "synthetic classes, budget is 5.0s -- possible redundant full-tree scan "
        "regression (task-8949/task-9011 pattern)"
    )
