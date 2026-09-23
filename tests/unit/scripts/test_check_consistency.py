"""scripts/check_consistency.py 단위 테스트 -- task-2850 CONSIST-1.

12종 검사 각각에 위반 fixture 1개 + 통과 fixture 1개(최소)를 tmp_path에
합성해 순수 함수 단위로 검증한다. `check_code_ratchets.py`와 같은 관례로
`importlib.util`을 통해 스크립트를 모듈로 로드한다(scripts/는 패키지가 아니다).
실제 파일 I/O(ast 파싱, 정규식)만 하고 DB·네트워크·subprocess(git)는 tmp_path가
저장소 밖이라 자연히 빈 값으로 폴백한다.

검사 로직 자체는 task-3725(CONSIST-1c)로 `scripts/consistency/` 검사군별
모듈로 옮겨졌다 -- `check_consistency.py`는 그 함수들을 재노출하는 CLI
진입점이다. `cc.check_*`는 재노출된 참조라 그대로 쓸 수 있지만,
`_git_commit_subjects`처럼 다른 모듈(`scripts.consistency.spec_trace`) 함수
안에서 그 모듈 자신의 전역으로 조회되는 이름을 monkeypatch할 때는 `cc` 위가
아니라 그 정의 모듈 위에서 패치해야 실제로 먹힌다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from scripts.consistency import spec_trace

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
        "class PostgresWidgetRepository:\n"
        "    async def get(self) -> None:\n        return None\n",
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
# 3. env_key_undocumented
# ---------------------------------------------------------------------------


def test_env_key_flags_undocumented_key(tmp_path: Path) -> None:
    _write(tmp_path, ".env.example", "OTHER_KEY=\n")
    _write(tmp_path, "src/foo.py", 'import os\nX = os.environ.get("SECRET_KEY")\n')
    hits = cc.check_env_keys(tmp_path)
    assert hits == [("src/foo.py", 2)]


def test_env_key_passes_when_documented(tmp_path: Path) -> None:
    _write(tmp_path, ".env.example", "SECRET_KEY=\n")
    _write(tmp_path, "src/foo.py", 'import os\nX = os.environ.get("SECRET_KEY")\n')
    assert cc.check_env_keys(tmp_path) == []


def test_env_key_resolves_indirection_via_module_constant(tmp_path: Path) -> None:
    _write(tmp_path, ".env.example", "OTHER_KEY=\n")
    _write(
        tmp_path,
        "src/foo.py",
        'import os\nMY_FLAG = "SOME_UNDOCUMENTED_FLAG"\nX = os.environ.get(MY_FLAG, "1")\n',
    )
    hits = cc.check_env_keys(tmp_path)
    assert hits == [("src/foo.py", 3)]


# ---------------------------------------------------------------------------
# 4. feature_flag_undocumented
# ---------------------------------------------------------------------------


def test_feature_flag_flags_undocumented_name(tmp_path: Path) -> None:
    _write(tmp_path, ".env.example", "OTHER_KEY=\n")
    _write(tmp_path, "src/foo.py", 'flag_enabled("MY_FLAG")\n')
    hits = cc.check_feature_flags(tmp_path)
    assert hits == [("src/foo.py", 1)]


def test_feature_flag_passes_when_documented(tmp_path: Path) -> None:
    _write(tmp_path, ".env.example", "MY_FLAG=\n")
    _write(tmp_path, "src/foo.py", 'flag_enabled("MY_FLAG")\n')
    assert cc.check_feature_flags(tmp_path) == []


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


# ---------------------------------------------------------------------------
# 6. migration_hygiene
# ---------------------------------------------------------------------------


def test_migration_flags_empty_downgrade(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/db/migrations/versions/0001_a.py",
        'revision = "0001"\ndown_revision = None\n\n'
        "def upgrade():\n    pass\n\ndef downgrade():\n    pass\n",
    )
    hits = cc.check_migrations(tmp_path)
    assert hits == [("src/db/migrations/versions/0001_a.py", 1)]


def test_migration_passes_with_real_downgrade(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/db/migrations/versions/0001_a.py",
        'revision = "0001"\ndown_revision = None\n\n'
        'def upgrade():\n    op.create_table("x")\n\n'
        'def downgrade():\n    op.drop_table("x")\n',
    )
    assert cc.check_migrations(tmp_path) == []


def test_migration_flags_multiple_heads(tmp_path: Path) -> None:
    body = "down_revision = None\n\ndef upgrade():\n    op.f()\n\ndef downgrade():\n    op.f()\n"
    _write(tmp_path, "src/db/migrations/versions/0001_a.py", f'revision = "0001"\n{body}')
    _write(tmp_path, "src/db/migrations/versions/0002_b.py", f'revision = "0002"\n{body}')
    hits = cc.check_migrations(tmp_path)
    assert ("src/db/migrations/versions", 0) in hits


# ---------------------------------------------------------------------------
# 7. openapi_client_mismatch
# ---------------------------------------------------------------------------


def test_openapi_flags_path_missing_from_frontend(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "contracts/openapi/v1.json",
        json.dumps({"paths": {"/foo/{id}": {}}}),
    )
    _write(
        tmp_path,
        "frontend/packages/api-client/src/apiRoutes.ts",
        'export const API_ROUTES = { "x": route("/bar", true) };\n',
    )
    hits = cc.check_openapi_frontend(tmp_path)
    assert ("contracts/openapi/v1.json#/foo/{id}", 0) in hits


def test_openapi_passes_when_path_registered(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "contracts/openapi/v1.json",
        json.dumps({"paths": {"/foo/{id}": {}}}),
    )
    _write(
        tmp_path,
        "frontend/packages/api-client/src/apiRoutes.ts",
        'export const API_ROUTES = { "x": route("/foo/:id", true) };\n',
    )
    assert cc.check_openapi_frontend(tmp_path) == []


def test_openapi_allowlisted_no_ui_path_is_not_flagged(tmp_path: Path) -> None:
    """task-3716: 서버 라우터는 있지만 화면이 없어 등록하지 않은 경로(예: /livez)는
    frontend에 등록되지 않아도 유령 경로로 오탐하지 않는다(apiPaths.openapi.test.ts의
    UNREGISTERED_ROUTE_WHITELIST와 동일 근거)."""
    _write(
        tmp_path,
        "contracts/openapi/v1.json",
        json.dumps({"paths": {"/livez": {}}}),
    )
    _write(
        tmp_path,
        "frontend/packages/api-client/src/apiRoutes.ts",
        "export const API_ROUTES = {};\n",
    )
    assert cc.check_openapi_frontend(tmp_path) == []


def test_openapi_removing_a_registered_non_allowlisted_path_is_still_flagged(
    tmp_path: Path,
) -> None:
    """task-3716 DoD(c): allowlist는 개별 경로 단위라 다른 경로까지 조용히 삼키지
    않는다 -- allowlist 경로(/livez)와 나란히 등록됐던 실제 경로(/foo/{id})의
    frontend 등록을 지우면 그 경로만 정확히 지목해 적색이 된다."""
    _write(
        tmp_path,
        "contracts/openapi/v1.json",
        json.dumps({"paths": {"/livez": {}, "/foo/{id}": {}}}),
    )
    _write(
        tmp_path,
        "frontend/packages/api-client/src/apiRoutes.ts",
        'export const API_ROUTES = { "x": route("/foo/:id", true) };\n',
    )
    assert cc.check_openapi_frontend(tmp_path) == []

    _write(
        tmp_path,
        "frontend/packages/api-client/src/apiRoutes.ts",
        "export const API_ROUTES = {};\n",
    )
    hits = cc.check_openapi_frontend(tmp_path)
    assert hits == [("contracts/openapi/v1.json#/foo/{id}", 0)]


def test_openapi_passes_for_compound_colon_verb_segment(tmp_path: Path) -> None:
    """task-3981: "{id}:verb" 복합 세그먼트(예: connections/{connection_id}:confirm)를
    frontend가 ":id:verb"로 등록하면 정상 매칭돼야 한다 -- 예전에는
    _normalize_legacy_path가 ":id:verb" 세그먼트 전체를 "*"로 뭉개 ":verb" 접미사를
    잃어버려서 이런 경로를 전부 유령으로 오탐했다."""
    _write(
        tmp_path,
        "contracts/openapi/v1.json",
        json.dumps({"paths": {"/foo/{id}:confirm": {}}}),
    )
    _write(
        tmp_path,
        "frontend/packages/api-client/src/apiRoutes.ts",
        'export const API_ROUTES = { "x": route("/foo/:id:confirm", true) };\n',
    )
    assert cc.check_openapi_frontend(tmp_path) == []


def test_openapi_flags_compound_colon_verb_mismatch(tmp_path: Path) -> None:
    """위 테스트의 반증: frontend가 다른 ":verb" 접미사(:approve)로 등록하면
    "*" 뭉개기로 우연히 통과하지 않고 여전히 양방향 모두 적색이어야 한다."""
    _write(
        tmp_path,
        "contracts/openapi/v1.json",
        json.dumps({"paths": {"/foo/{id}:confirm": {}}}),
    )
    _write(
        tmp_path,
        "frontend/packages/api-client/src/apiRoutes.ts",
        'export const API_ROUTES = { "x": route("/foo/:id:approve", true) };\n',
    )
    hits = cc.check_openapi_frontend(tmp_path)
    assert ("contracts/openapi/v1.json#/foo/{id}:confirm", 0) in hits
    assert ("frontend/packages/api-client/src/apiRoutes.ts#/foo/:id:approve", 0) in hits


# ---------------------------------------------------------------------------
# 8. spec_leaf_untraced
# ---------------------------------------------------------------------------


def test_spec_leaf_flags_untraced_id(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/specs/L4_x_v1.0.md",
        "## 9. 리프 목록\n| 리프 ID | 파일 |\n|---|---|\n| Z-99 | src/z.py |\n",
    )
    hits = cc.check_spec_leaf_traceability(tmp_path)
    assert hits == [("docs/specs#Z-99", 0)]


def test_spec_leaf_passes_when_referenced_in_code(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/specs/L4_x_v1.0.md",
        "## 9. 리프 목록\n| 리프 ID | 파일 |\n|---|---|\n| Z-99 | src/z.py |\n",
    )
    _write(tmp_path, "src/z.py", "# Z-99 구현\nx = 1\n")
    assert cc.check_spec_leaf_traceability(tmp_path) == []


def test_spec_leaf_expands_range_tokens(tmp_path: Path) -> None:
    assert cc._expand_leaf_ids("R-05~R-07") == ["R-05", "R-06", "R-07"]


def test_collect_spec_leaf_ids_ignores_rows_inside_status_block(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/specs/L4_x_v1.0.md",
        "## 9. 리프 목록\n| 리프 ID | 파일 |\n|---|---|\n| EO-01 | src/eo.py |\n\n"
        "## 11. 진행 현황 (자동 생성)\n\n"
        "<!-- spec-status:begin (auto-generated by C:/aios/pm/spec_status.py -- do not edit) -->\n"
        "| ID | 리프 | 상태 | commit |\n|---|---|---|---|\n"
        "| EO-1 | src/eo.py | done | abc123 |\n"
        "<!-- spec-status:end -->\n",
    )
    ids = cc._collect_spec_leaf_ids(tmp_path / "docs" / "specs")
    assert ids == {"EO-01"}


def test_spec_leaf_flags_id_that_is_prefix_of_another_traced_id(tmp_path: Path) -> None:
    # task-3680: substring 매칭("AI-2 in code_blob")은 AI-2가 AI-22의 접두라서
    # AI-22만 구현된 코드를 보고도 AI-2를 "추적됨"으로 오판했다(esc-ci-6e80c35b1015).
    _write(
        tmp_path,
        "docs/specs/L4_x_v1.0.md",
        "## 9. 리프 목록\n| 리프 ID | 파일 |\n|---|---|\n"
        "| Z-1 | src/z1.py |\n| Z-10 | src/z10.py |\n",
    )
    _write(tmp_path, "src/z10.py", "# Z-10 구현\nx = 1\n")
    hits = cc.check_spec_leaf_traceability(tmp_path)
    assert hits == [("docs/specs#Z-1", 0)]


def test_spec_leaf_flags_id_that_is_prefix_of_id_in_commit_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "docs/specs/L4_x_v1.0.md",
        "## 9. 리프 목록\n| 리프 ID | 파일 |\n|---|---|\n| AI-2 | src/token.py |\n",
    )
    monkeypatch.setattr(
        spec_trace, "_git_commit_subjects", lambda root: "feat: task-2657 AI-22 AiStudioPage\n"
    )
    hits = cc.check_spec_leaf_traceability(tmp_path)
    assert hits == [("docs/specs#AI-2", 0)]


def test_spec_leaf_passes_when_id_appears_as_whole_token_in_commit_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "docs/specs/L4_x_v1.0.md",
        "## 9. 리프 목록\n| 리프 ID | 파일 |\n|---|---|\n| AI-2 | src/token.py |\n",
    )
    monkeypatch.setattr(
        spec_trace, "_git_commit_subjects", lambda root: "feat: task-2600 AI-2 token_rules.py\n"
    )
    assert cc.check_spec_leaf_traceability(tmp_path) == []


def test_spec_leaf_passes_when_status_block_duplicates_unpadded_id(tmp_path: Path) -> None:
    # task-3629 CONSIST-1: spec_status.py의 자동생성 §11 표는 norm_leaf_id로 선행 0을
    # 뗀 ID를 쓴다(EO-1). §9는 패딩형(EO-01)이라 코드/커밋 검색엔 EO-01만 등장하므로,
    # 이 블록을 리프로 잘못 집계하면 EO-1이 미추적으로 오탐된다.
    _write(
        tmp_path,
        "docs/specs/L4_x_v1.0.md",
        "## 9. 리프 목록\n| 리프 ID | 파일 |\n|---|---|\n| EO-01 | src/eo.py |\n\n"
        "## 11. 진행 현황 (자동 생성)\n\n"
        "<!-- spec-status:begin (auto-generated by C:/aios/pm/spec_status.py -- do not edit) -->\n"
        "| ID | 리프 | 상태 | commit |\n|---|---|---|---|\n"
        "| EO-1 | src/eo.py | done | abc123 |\n"
        "<!-- spec-status:end -->\n",
    )
    _write(tmp_path, "src/eo.py", "# EO-01 구현\nx = 1\n")
    assert cc.check_spec_leaf_traceability(tmp_path) == []


# ---------------------------------------------------------------------------
# 9. naive_datetime
# ---------------------------------------------------------------------------


def test_naive_datetime_flags_now_without_tz(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.now()\n")
    hits = cc.check_naive_datetime(tmp_path)
    assert hits == [("src/foo.py", 2)]


def test_naive_datetime_passes_with_tz(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nx = datetime.datetime.now(datetime.timezone.utc)\n",
    )
    assert cc.check_naive_datetime(tmp_path) == []


def test_naive_datetime_flags_utcnow_always(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.utcnow()\n")
    assert cc.check_naive_datetime(tmp_path) == [("src/foo.py", 2)]


# ---------------------------------------------------------------------------
# 10. money_float
# ---------------------------------------------------------------------------


def test_money_float_flags_float_annotation(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "def f(amount: float) -> None:\n    pass\n")
    hits = cc.check_money_float(tmp_path)
    assert hits == [("src/foo.py", 1)]


def test_money_float_passes_with_decimal(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "from decimal import Decimal\ndef f(amount: Decimal) -> None:\n    pass\n",
    )
    assert cc.check_money_float(tmp_path) == []


def test_money_float_regression_task_3828_sites_stay_clean() -> None:
    """task-3828 회귀 가드 -- CI(ci/48ec858bf68c) 적색의 실제 원인이었던 세 필드
    (`LiquidationPolicy.max_slice_notional`, `metrics_registry.Counter/Gauge`의
    `amount` 파라미터, `StrategyIntent.qty`)가 다시 float로 되돌아가면 실제
    저장소(tmp_path 합성이 아니라 ROOT)를 스캔하는 이 테스트가 즉시 잡는다."""
    hits = dict(cc.check_money_float(ROOT))
    assert "src/core/loader/risk_policy_loader.py" not in hits
    assert "src/core/observability/metrics_registry.py" not in hits
    assert "src/core/script/runtime/builtins_strategy.py" not in hits


def test_money_float_regression_task_4188_krx_data_stays_clean() -> None:
    """task-4188 회귀 가드 -- money_float이 10 -> 13으로 재발한 원인은 RD-12
    산출물 `src/foundation/market_data/adapters/krx_data.py`의 `IndexQuote.price`/
    `IndexPoint.price`/`ShortResistanceStock.price` 세 필드가 float로 선언된
    것이었다(mandates/contracts, risk/contracts의 나머지 10건은 OpenAPI 응답
    스키마라 task-3828 때부터 baseline 부채로 남겨둔 것과 별개). 세 필드를
    다시 float로 되돌리면 실제 저장소를 스캔하는 이 테스트가 즉시 잡는다."""
    hits = dict(cc.check_money_float(ROOT))
    assert "src/foundation/market_data/adapters/krx_data.py" not in hits


# ---------------------------------------------------------------------------
# 11. symbol_id_assembly
# ---------------------------------------------------------------------------


def test_symbol_assembly_flags_fstring(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        'def build(venue, code):\n    symbol = f"{venue}:{code}"\n    return symbol\n',
    )
    hits = cc.check_symbol_id_assembly(tmp_path)
    assert hits == [("src/foo.py", 2)]


def test_symbol_assembly_passes_on_passthrough(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "def build(record):\n    symbol = record.symbol\n    return symbol\n",
    )
    assert cc.check_symbol_id_assembly(tmp_path) == []


def test_symbol_assembly_exempts_migrations_dir(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/db/migrations/versions/0001_a.py",
        'symbol = venue + ":" + code\n',
    )
    assert cc.check_symbol_id_assembly(tmp_path) == []


# ---------------------------------------------------------------------------
# 12. spec_template_incomplete
# ---------------------------------------------------------------------------


def test_spec_template_flags_missing_sections(tmp_path: Path) -> None:
    _write(tmp_path, "docs/specs/L4_x_v1.0.md", "# 명세\n내용만 있고 절 구분이 없다.\n")
    hits = cc.check_spec_template(tmp_path)
    assert ("docs/specs/L4_x_v1.0.md", 0) in hits


def test_spec_template_passes_with_required_sections(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/specs/L4_x_v1.0.md",
        "# 명세\n## 9. 리프 목록\n표\n## 10. 미확정·리스크\n없음\n",
    )
    assert cc.check_spec_template(tmp_path) == []


# ---------------------------------------------------------------------------
# 13. authority_duplication (RATCHET-2, task-3256)
# ---------------------------------------------------------------------------


def test_authority_duplication_flags_same_context_duplicate_assembly(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foundation/positions/domain/journal_rules.py",
        "def fill_entry(order_id, fill_seq):\n"
        '    idempotency_key = f"fill:{order_id}:{fill_seq}"\n'
        "    return idempotency_key\n",
    )
    _write(
        tmp_path,
        "src/foundation/positions/application/record_fill.py",
        "def check_exists(command):\n"
        '    idempotency_key = f"fill:{command.order_id}:{command.fill_seq}"\n'
        "    return idempotency_key\n",
    )
    hits = cc.check_authority_duplication(tmp_path)
    assert hits == [
        ("src/foundation/positions/application/record_fill.py", 2),
        ("src/foundation/positions/domain/journal_rules.py", 2),
    ]


def test_authority_duplication_passes_on_single_builder_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foundation/ledger/domain/idempotency.py",
        "def idempotency_key(event):\n"
        '    key = f"{event.event_type}:{event.event_ref}"\n'
        "    return key\n",
    )
    assert cc.check_authority_duplication(tmp_path) == []


def test_authority_duplication_passes_across_different_bounded_contexts(tmp_path: Path) -> None:
    """다른 bounded context(서로 다른 애그리게잇)에서 같은 이름을 각자 조립하는
    것은 위반이 아니다 -- 의도적으로 분리된 authority(연구 결과: risk_gate/
    mandates/risk는 3개의 독립된 bounded context)."""
    _write(
        tmp_path,
        "src/foundation/positions/domain/journal_rules.py",
        'def fill_entry(order_id):\n    idempotency_key = f"fill:{order_id}"\n'
        "    return idempotency_key\n",
    )
    _write(
        tmp_path,
        "src/foundation/paper_control/application/apply_safety_control.py",
        "def apply(control_id):\n"
        '    idempotency_key = f"risk-pause:{control_id}"\n'
        "    return idempotency_key\n",
    )
    assert cc.check_authority_duplication(tmp_path) == []


def test_authority_duplication_passes_on_delegation_not_raw_assembly(tmp_path: Path) -> None:
    """캔노니컬 빌더를 호출해 전달받는 것(위임)은 위반이 아니다 -- raw assembly만 잡는다."""
    _write(
        tmp_path,
        "src/foundation/positions/domain/journal_rules.py",
        'def fill_entry(order_id):\n    idempotency_key = f"fill:{order_id}"\n'
        "    return idempotency_key\n",
    )
    _write(
        tmp_path,
        "src/foundation/positions/application/record_fill.py",
        "from src.foundation.positions.domain.journal_rules import fill_entry\n"
        "def check_exists(command):\n"
        "    entry = fill_entry(command.order_id)\n"
        "    idempotency_key = entry.idempotency_key\n"
        "    return idempotency_key\n",
    )
    assert cc.check_authority_duplication(tmp_path) == []


def test_authority_duplication_exempts_migrations_dir(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/db/migrations/versions/0001_a.py",
        'idempotency_key = f"fill:{x}"\n',
    )
    _write(
        tmp_path,
        "src/db/migrations/versions/0002_b.py",
        'idempotency_key = f"fill:{y}"\n',
    )
    assert cc.check_authority_duplication(tmp_path) == []


# ---------------------------------------------------------------------------
# main() -- 래칫 DoD
# ---------------------------------------------------------------------------


def _write_baseline(tmp_path: Path, counts: dict[str, int]) -> Path:
    path = tmp_path / "consistency-baseline.json"
    full = {m: counts.get(m, 0) for m in cc.METRICS}
    path.write_text(json.dumps(full), encoding="utf-8")
    return path


def test_main_first_run_initializes_baseline(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.utcnow()\n")
    baseline_path = tmp_path / "consistency-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 1
    assert set(data) == set(cc.METRICS)


def test_main_increase_fails_red(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nx = datetime.datetime.utcnow()\ny = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 1})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "naive_datetime" in out
    unchanged = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert unchanged["naive_datetime"] == 1


def test_main_decrease_without_update_keeps_baseline(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 3


def test_main_decrease_with_update_ratchets_down(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 0


def test_main_malformed_baseline_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text("not json", encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


def test_main_baseline_missing_metric_key_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text(json.dumps({"naive_datetime": 0}), encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1
