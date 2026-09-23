"""CONSIST-1 계약 검사군 -- env_key_undocumented / feature_flag_undocumented /
event_type_unconsumed / migration_hygiene / openapi_client_mismatch. task-3725
CONSIST-1c로 check_consistency.py에서 분리(순수 이동, 판정 로직 변경 없음).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from scripts.consistency.common import (
    Hit,
    _is_docstring_stmt,
    _iter_py_files,
    _module_level_literal_consts,
    _parse_env_example_keys,
    _resolve_seq,
    _resolve_str,
    _safe_parse,
)

# ---------------------------------------------------------------------------
# 3/4. env_key_undocumented / feature_flag_undocumented
# ---------------------------------------------------------------------------


def _extract_environ_key(
    node: ast.Subscript | ast.Call, consts: dict[str, list[str]]
) -> str | None:
    if isinstance(node, ast.Subscript):
        val = node.value
        if (
            isinstance(val, ast.Attribute)
            and val.attr == "environ"
            and isinstance(node.slice, ast.expr)
        ):
            return _resolve_str(node.slice, consts)
        return None
    if not node.args:
        return None
    func = node.func
    is_environ_get = (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "environ"
    )
    is_getenv = (isinstance(func, ast.Attribute) and func.attr == "getenv") or (
        isinstance(func, ast.Name) and func.id == "getenv"
    )
    if not (is_environ_get or is_getenv):
        return None
    return _resolve_str(node.args[0], consts)


def check_env_keys(root: Path) -> list[Hit]:
    env_example = root / ".env.example"
    if not env_example.exists():
        return []
    documented = _parse_env_example_keys(env_example)
    hits: list[Hit] = []
    for path in _iter_py_files(root, "src"):
        tree = _safe_parse(path)
        if tree is None:
            continue
        consts = _module_level_literal_consts(tree)
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript | ast.Call):
                continue
            key = _extract_environ_key(node, consts)
            if key is not None and key not in documented:
                hits.append((rel, node.lineno))
    return hits


def check_feature_flags(root: Path) -> list[Hit]:
    env_example = root / ".env.example"
    if not env_example.exists():
        return []
    documented = _parse_env_example_keys(env_example)
    hits: list[Hit] = []
    for path in _iter_py_files(root, "src"):
        tree = _safe_parse(path)
        if tree is None:
            continue
        consts = _module_level_literal_consts(tree)
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "flag_enabled"
                and node.args
            ):
                name = _resolve_str(node.args[0], consts)
                if name is not None and name not in documented:
                    hits.append((rel, node.lineno))
    return hits


# ---------------------------------------------------------------------------
# 5. event_type_unconsumed
# ---------------------------------------------------------------------------


def _walk_pub_sub(
    node: ast.AST,
    consts: dict[str, list[str]],
    scope: dict[str, list[str]],
    rel: str,
    published: dict[str, Hit],
    consumed: set[str],
) -> None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
        if node.func.attr == "publish":
            for topic in _resolve_seq(node.args[0], consts, scope) or []:
                published.setdefault(topic, (rel, node.lineno))
        elif node.func.attr == "subscribe":
            for topic in _resolve_seq(node.args[0], consts, scope) or []:
                consumed.add(topic)
    if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
        values = _resolve_seq(node.iter, consts, scope)
        child_scope = dict(scope)
        if values is not None:
            child_scope[node.target.id] = values
        for body_stmt in node.body:
            _walk_pub_sub(body_stmt, consts, child_scope, rel, published, consumed)
        for else_stmt in node.orelse:
            _walk_pub_sub(else_stmt, consts, scope, rel, published, consumed)
        return
    for child in ast.iter_child_nodes(node):
        _walk_pub_sub(child, consts, scope, rel, published, consumed)


def check_event_consumers(root: Path) -> list[Hit]:
    published: dict[str, Hit] = {}
    consumed: set[str] = set()
    for path in _iter_py_files(root, "src"):
        tree = _safe_parse(path)
        if tree is None:
            continue
        consts = _module_level_literal_consts(tree)
        rel = path.relative_to(root).as_posix()
        _walk_pub_sub(tree, consts, {}, rel, published, consumed)
    return [hit for topic, hit in sorted(published.items()) if topic not in consumed]


# ---------------------------------------------------------------------------
# 6. migration_hygiene
# ---------------------------------------------------------------------------


def _assign_target_name(node: ast.Assign) -> str | None:
    if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def _assign_str_value(node: ast.Assign) -> str | None:
    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        return node.value.value
    return None


def _downgrade_has_body(node: ast.FunctionDef) -> bool:
    body = [s for s in node.body if not _is_docstring_stmt(s)]
    return any(
        not isinstance(s, ast.Pass)
        and not (
            isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is ...
        )
        for s in body
    )


def _migration_revision_info(tree: ast.Module) -> tuple[str | None, str | None, bool]:
    revision: str | None = None
    down_revision: str | None = None
    has_downgrade_body = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            name = _assign_target_name(node)
            if name == "revision":
                revision = _assign_str_value(node)
            elif name == "down_revision":
                down_revision = _assign_str_value(node)
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            has_downgrade_body = _downgrade_has_body(node)
    return revision, down_revision, has_downgrade_body


def check_migrations(root: Path) -> list[Hit]:
    versions_dir = root / "src" / "db" / "migrations" / "versions"
    if not versions_dir.is_dir():
        return []
    hits: list[Hit] = []
    down_revision_of: dict[str, str | None] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        revision, down_revision, has_downgrade_body = _migration_revision_info(tree)
        if revision is None:
            continue
        if not has_downgrade_body:
            hits.append((rel, 1))
        down_revision_of[revision] = down_revision
    referenced = {v for v in down_revision_of.values() if v}
    heads = [r for r in down_revision_of if r not in referenced]
    if len(heads) >= 2:
        hits.append((versions_dir.relative_to(root).as_posix(), 0))
    return hits


# ---------------------------------------------------------------------------
# 7. openapi_client_mismatch
# ---------------------------------------------------------------------------

_OPENAPI_PARAM_RE = re.compile(r"\{[^}]+\}")
_ROUTE_CALL_RE = re.compile(r'route\(\s*"([^"]+)"')

# task-3981: ":id:verb" 복합 세그먼트(예: "{connection_id}:confirm")는
# frontend/packages/api-client/src/apiPaths.openapi.scanner.ts의
# legacyPathToTemplate()과 동일한 규칙으로 _normalize_legacy_path()가 처리한다
# (":paramName" 접두만 "*"로 치환하고 뒤에 붙은 ":verb" 리터럴은 보존) -- 예전에는
# 이 정규화가 세그먼트 전체를 "*"로 뭉개 복합 세그먼트를 유령 경로로 오탐했다
# (task-3850이 그 오탐 1건을 아래 화이트리스트에 개별 우회로 남겼던 이유). 지금은
# _normalize_legacy_path()가 이를 올바르게 처리하므로 그런 우회는 더 필요 없다.
#
# 아래 화이트리스트는 그와는 다른, 진짜 사유("서버 라우터는 있지만 화면이 아직 없어
# apiRoutes.ts에 의도적으로 등록하지 않은" 경로)만 남긴다. frontend/packages/
# api-client/src/apiPaths.openapi.test.ts의 UNREGISTERED_ROUTE_WHITELIST
# (task-2168 §E)와 정확히 같은 경로·같은 사유다 -- 두 목록이 갈라지면 그 vitest의
# "화이트리스트 부패 방지" 테스트가 먼저 잡는다. 값은 "서버 라우터 파일:라인 -- 사유".
_OPENAPI_NO_FRONTEND_UI_ALLOWLIST: dict[str, str] = {
    "/admin/audit-log": "src/api/routers/admin.py:73 -- 관리자 감사 로그 화면 없음",
    "/admin/break-glass/grants": (
        "src/api/routers/admin_break_glass.py:30 -- break-glass grant 요청 콘솔 UI 없음 (task-3850)"
    ),
    "/admin/break-glass/grants/{grant_id}:approve": (
        "src/api/routers/admin_break_glass.py:44 -- break-glass grant 승인 콘솔 UI 없음 (task-3850)"
    ),
    "/admin/ledger/payouts/{batch_id}/paid": (
        "src/api/routers/foundation/ledger_admin.py:46 -- 정산 배치 확정 액션 UI 없음"
    ),
    "/exchange-credentials/{exchange}/positions": (
        "src/api/routers/exchange_credentials.py:97 -- exchange.ts에 positions 조회 없음"
    ),
    "/livez": "src/api/routers/health.py:79 -- k8s liveness 프로브, 앱 API 표면 아님",
    "/metrics": "src/api/routers/metrics.py:36 -- 모니터링 전용 엔드포인트, 앱 API 표면 아님",
    "/readyz": "src/api/routers/health.py:85 -- k8s readiness 프로브, 앱 API 표면 아님",
    "/v1/foundation/performance-statements": (
        "src/api/routers/foundation/performance.py:94 -- 실적 명세서 화면 없음"
    ),
    "/v1/foundation/performance-statements/{statement_id}": (
        "src/api/routers/foundation/performance.py:112 -- 실적 명세서 화면 없음"
    ),
    "/v1/foundation/performance-statements/{statement_id}:correct": (
        "src/api/routers/foundation/performance.py:130 -- 실적 명세서 정정 액션 UI 없음"
    ),
    "/v1/foundation/performance-statements:compute": (
        "src/api/routers/foundation/performance.py:65 -- 실적 명세서 계산 액션 UI 없음"
    ),
    "/v1/foundation/reconciliation/runs": (
        "src/api/routers/foundation/reconciliation.py:50 -- 정합성 대사 실행 이력 화면 없음"
    ),
    "/v1/foundation/ems/tca/{parent_id}": (
        "src/api/routers/foundation/ems.py:81 -- TCA 리포트 화면 없음(EM-18 후속)"
    ),
    "/v1/foundation/ems/tca/{parent_id}/revisions/{revision}": (
        "src/api/routers/foundation/ems.py:93 -- TCA 리포트 화면 없음(EM-18 후속)"
    ),
    "/v1/foundation/ems/tca/{parent_id}:compute": (
        "src/api/routers/foundation/ems.py:42 -- TCA 계산 트리거 화면 없음(EM-18 후속)"
    ),
    "/v1/foundation/risk-gate/admin/safety-controls": (
        "src/api/routers/foundation/risk_gate.py:203 -- 리스크 게이트 관리자 개통 UI 없음"
    ),
    "/v1/foundation/risk-gate/evaluate": (
        "src/api/routers/foundation/risk_gate.py:78 -- 리스크 게이트 평가 트리거 화면 없음"
    ),
    "/v1/foundation/risk-gate/rule-bundles/{bundle_id}:activate": (
        "src/api/routers/foundation/risk_gate.py:259 -- 룰번들 활성화 액션 UI 없음"
    ),
    "/v1/foundation/risk-gate/rule-bundles/{bundle_id}:approve": (
        "src/api/routers/foundation/risk_gate.py:240 -- 룰번들 승인 액션 UI 없음"
    ),
}


def _normalize_openapi_path(p: str) -> str:
    return _OPENAPI_PARAM_RE.sub("*", p)


def _normalize_legacy_segment(seg: str) -> str:
    if not seg.startswith(":"):
        return seg
    parts = seg[1:].split(":")
    return "*" if len(parts) == 1 else f"*:{':'.join(parts[1:])}"


def _normalize_legacy_path(p: str) -> str:
    return "/".join(_normalize_legacy_segment(seg) for seg in p.split("/"))


def check_openapi_frontend(root: Path) -> list[Hit]:
    openapi_path = root / "contracts" / "openapi" / "v1.json"
    routes_files = [
        root / "frontend" / "packages" / "api-client" / "src" / "apiRoutes.ts",
        root / "frontend" / "packages" / "api-client" / "src" / "apiRoutesFoundationOps.ts",
    ]
    if not openapi_path.exists() or not any(f.exists() for f in routes_files):
        return []
    try:
        spec = json.loads(openapi_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    openapi_paths: set[str] = set(spec.get("paths", {}).keys())
    openapi_norm = {_normalize_openapi_path(p) for p in openapi_paths}
    frontend_paths: list[str] = []
    for f in routes_files:
        if f.exists():
            frontend_paths.extend(_ROUTE_CALL_RE.findall(f.read_text(encoding="utf-8")))
    frontend_norm = {_normalize_legacy_path(p) for p in frontend_paths}
    rel_openapi = openapi_path.relative_to(root).as_posix()
    rel_frontend = routes_files[0].relative_to(root).as_posix()
    hits: list[Hit] = []
    for p in sorted(openapi_paths):
        if p in _OPENAPI_NO_FRONTEND_UI_ALLOWLIST:
            continue
        if _normalize_openapi_path(p) not in frontend_norm:
            hits.append((f"{rel_openapi}#{p}", 0))
    for p in sorted(set(frontend_paths)):
        if _normalize_legacy_path(p) not in openapi_norm:
            hits.append((f"{rel_frontend}#{p}", 0))
    return hits
