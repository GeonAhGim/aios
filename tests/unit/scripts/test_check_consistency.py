"""scripts/check_consistency.py 단위 테스트 -- task-2850 CONSIST-1.

12종 검사 각각에 위반 fixture 1개 + 통과 fixture 1개(최소)를 tmp_path에
합성해 순수 함수 단위로 검증한다. `check_code_ratchets.py`와 같은 관례로
`importlib.util`을 통해 스크립트를 모듈로 로드한다(scripts/는 패키지가 아니다).
실제 파일 I/O(ast 파싱, 정규식)만 하고 DB·네트워크·subprocess(git)는 tmp_path가
저장소 밖이라 자연히 빈 값으로 폴백한다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

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
