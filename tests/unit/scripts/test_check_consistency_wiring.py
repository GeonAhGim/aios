"""Router, adapter port and event-consumer wiring -- task-10846, CONSIST-1.

Tests stay grouped by the consistency checker responsibility.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_module("check_consistency", SCRIPTS_DIR / "check_consistency.py")


def _write(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path




# ---------------------------------------------------------------------------
# 1. router_unregistered
# ---------------------------------------------------------------------------


def test_router_flags_unregistered_router(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/api/routers/foo.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n",
    )
    hits = cc.check_router_wiring(tmp_path)
    assert hits == [("src/api/routers/foo.py", 1)]


def test_router_passes_when_included(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/api/routers/foo.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n",
    )
    _write(
        tmp_path,
        "src/api/router_registry.py",
        "def register_routers(app):\n"
        "    from src.api.routers import foo\n"
        "    app.include_router(foo.router)\n",
    )
    assert cc.check_router_wiring(tmp_path) == []


# ---------------------------------------------------------------------------
# 2. port_method_unimplemented
# ---------------------------------------------------------------------------

_PORT_SRC = (
    "from abc import ABC, abstractmethod\n\n"
    "class Port(ABC):\n"
    "    @abstractmethod\n"
    "    def do(self) -> None: ...\n"
)


def test_port_flags_missing_method(tmp_path: Path) -> None:
    _write(tmp_path, "src/ports.py", _PORT_SRC)
    _write(
        tmp_path,
        "src/adapter.py",
        "from src.ports import Port\n\nclass Adapter(Port):\n    pass\n",
    )
    hits = cc.check_port_implementations(tmp_path)
    assert hits == [("src/adapter.py", 3)]


def test_port_passes_when_implemented(tmp_path: Path) -> None:
    _write(tmp_path, "src/ports.py", _PORT_SRC)
    _write(
        tmp_path,
        "src/adapter.py",
        "from src.ports import Port\n\nclass Adapter(Port):\n"
        "    def do(self) -> None:\n        return None\n",
    )
    assert cc.check_port_implementations(tmp_path) == []


def test_port_flags_not_implemented_stub_without_ratchet_allow(tmp_path: Path) -> None:
    _write(tmp_path, "src/ports.py", _PORT_SRC)
    _write(
        tmp_path,
        "src/adapter.py",
        "from src.ports import Port\n\nclass Adapter(Port):\n"
        "    def do(self) -> None:\n        raise NotImplementedError\n",
    )
    hits = cc.check_port_implementations(tmp_path)
    assert len(hits) == 1


def test_port_ratchet_allow_exempts_not_implemented_stub(tmp_path: Path) -> None:
    _write(tmp_path, "src/ports.py", _PORT_SRC)
    _write(
        tmp_path,
        "src/adapter.py",
        "# ratchet-allow: fail-closed stub, intentional\n"
        "from src.ports import Port\n\nclass Adapter(Port):\n"
        "    def do(self) -> None:\n        raise NotImplementedError\n",
    )
    assert cc.check_port_implementations(tmp_path) == []


# ---------------------------------------------------------------------------
# 2b. port_protocol_unimplemented
# ---------------------------------------------------------------------------

_PROTOCOL_PORT_SRC = (
    "from typing import Protocol\n\n"
    "class WidgetRepository(Protocol):\n"
    "    async def get(self) -> None: ...\n"
    "    async def save(self) -> None: ...\n"
)


def test_protocol_port_flags_missing_method(tmp_path: Path) -> None:
    _write(tmp_path, "src/ctx/ports/repository.py", _PROTOCOL_PORT_SRC)
    _write(
        tmp_path,
        "src/ctx/adapters/postgres_repository.py",
        "class PostgresWidgetRepository:\n    async def get(self) -> None:\n        return None\n",
    )
    hits = cc.check_port_protocol_implementations(tmp_path)
    assert hits == [("src/ctx/adapters/postgres_repository.py", 1)]


def test_protocol_port_passes_when_implemented(tmp_path: Path) -> None:
    _write(tmp_path, "src/ctx/ports/repository.py", _PROTOCOL_PORT_SRC)
    _write(
        tmp_path,
        "src/ctx/adapters/postgres_repository.py",
        "class PostgresWidgetRepository:\n"
        "    async def get(self) -> None:\n        return None\n"
        "    async def save(self) -> None:\n        return None\n",
    )
    assert cc.check_port_protocol_implementations(tmp_path) == []


def test_protocol_port_flags_not_implemented_stub_without_ratchet_allow(tmp_path: Path) -> None:
    _write(tmp_path, "src/ctx/ports/repository.py", _PROTOCOL_PORT_SRC)
    _write(
        tmp_path,
        "src/ctx/adapters/postgres_repository.py",
        "class PostgresWidgetRepository:\n"
        "    async def get(self) -> None:\n        return None\n"
        "    async def save(self) -> None:\n        raise NotImplementedError\n",
    )
    hits = cc.check_port_protocol_implementations(tmp_path)
    assert len(hits) == 1


def test_protocol_port_ratchet_allow_exempts_not_implemented_stub(tmp_path: Path) -> None:
    _write(tmp_path, "src/ctx/ports/repository.py", _PROTOCOL_PORT_SRC)
    _write(
        tmp_path,
        "src/ctx/adapters/postgres_repository.py",
        "# ratchet-allow: fail-closed stub, intentional\n"
        "class PostgresWidgetRepository:\n"
        "    async def get(self) -> None:\n        return None\n"
        "    async def save(self) -> None:\n        raise NotImplementedError\n",
    )
    assert cc.check_port_protocol_implementations(tmp_path) == []


def test_protocol_port_resolves_local_mixin_inheritance(tmp_path: Path) -> None:
    """task-1723 P1-D 스타일 분할 -- adapter가 같은 adapters/ 컨텍스트의
    mixin에서 메서드를 상속받으면 과탐(false positive)하지 않는다."""
    _write(tmp_path, "src/ctx/ports/repository.py", _PROTOCOL_PORT_SRC)
    _write(
        tmp_path,
        "src/ctx/adapters/save_mixin.py",
        "class _SaveMixin:\n    async def save(self) -> None:\n        return None\n",
    )
    _write(
        tmp_path,
        "src/ctx/adapters/postgres_repository.py",
        "from src.ctx.adapters.save_mixin import _SaveMixin\n\n"
        "class PostgresWidgetRepository(_SaveMixin):\n"
        "    async def get(self) -> None:\n        return None\n",
    )
    assert cc.check_port_protocol_implementations(tmp_path) == []


def test_protocol_port_ignores_adapter_in_unrelated_bounded_context(tmp_path: Path) -> None:
    _write(tmp_path, "src/ctx_a/ports/repository.py", _PROTOCOL_PORT_SRC)
    _write(
        tmp_path,
        "src/ctx_b/adapters/postgres_repository.py",
        "class PostgresWidgetRepository:\n    pass\n",
    )
    assert cc.check_port_protocol_implementations(tmp_path) == []


def test_protocol_port_ignores_adapter_class_name_not_matching_any_port(tmp_path: Path) -> None:
    _write(tmp_path, "src/ctx/ports/repository.py", _PROTOCOL_PORT_SRC)
    _write(
        tmp_path,
        "src/ctx/adapters/unrelated.py",
        "class SomethingElseEntirely:\n    pass\n",
    )
    assert cc.check_port_protocol_implementations(tmp_path) == []


# ---------------------------------------------------------------------------
# 5. event_type_unconsumed
# ---------------------------------------------------------------------------


def test_event_flags_published_topic_without_consumer(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "TOPIC = 'x.y'\n\nasync def f(bus):\n    await bus.publish(TOPIC, {})\n",
    )
    hits = cc.check_event_consumers(tmp_path)
    assert hits == [("src/foo.py", 4)]


def test_event_passes_when_consumed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "TOPIC = 'x.y'\n\n"
        "async def f(bus):\n"
        "    await bus.publish(TOPIC, {})\n\n"
        "def g(bus, handler):\n"
        "    bus.subscribe(TOPIC, handler)\n",
    )
    assert cc.check_event_consumers(tmp_path) == []


def test_event_resolves_topics_from_loop_over_tuple_constant(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "TOPICS = ('a.b', 'c.d')\n\n"
        "async def f(bus):\n"
        "    await bus.publish('a.b', {})\n"
        "    await bus.publish('c.d', {})\n\n"
        "def g(bus, handler):\n"
        "    for t in TOPICS:\n"
        "        bus.subscribe(t, handler)\n",
    )
    assert cc.check_event_consumers(tmp_path) == []
