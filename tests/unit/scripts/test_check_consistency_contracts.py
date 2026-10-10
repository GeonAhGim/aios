"""Environment, migration and frontend API contracts -- task-10846, CONSIST-1.

Tests stay grouped by the consistency checker responsibility.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_openapi_allowlist_partial_spelling_mismatch_still_flagged(tmp_path: Path) -> None:
    """task-4089 ND-21 경계 negative #3: allowlist는 정밀 일치만 적용된다.
    트레일링 슬래시(/livez/), 대소문자(/LIVEZ), 오타(/livezz)는 허용되지
    않아야 한다 — whitelist 철자가 살짝 달라도 무단 통과하면 안 된다."""
    # /livez 는 allowlist에 있지만, /livez/ 와 /LIVEZ 는 아님
    _write(
        tmp_path,
        "contracts/openapi/v1.json",
        json.dumps({"paths": {"/livez": {}, "/livez/": {}, "/LIVEZ": {}}}),
    )
    _write(
        tmp_path,
        "frontend/packages/api-client/src/apiRoutes.ts",
        "export const API_ROUTES = {};\n",
    )
    hits = cc.check_openapi_frontend(tmp_path)
    # /livez 만 allowlist 면제 — /livez/ 와 /LIVEZ 는 적색
    assert len(hits) == 2
    hit_keys = {key for key, _ in hits}
    assert any("livez/" in k for k in hit_keys)
    assert any("/LIVEZ" in k for k in hit_keys)


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
