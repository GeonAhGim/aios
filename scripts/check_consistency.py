"""CONSIST-1 배선 일관성 검사 스위트 — task-2850.

대시보드 파서가 "§9"를 가정해 명세 1종을 놓친 사고처럼, 코드에도 "당연히
그렇겠지"가 박힌 배선 누락이 있다. 이 스크립트는 그런 12종의 기계 판정 가능한
누락을 정적 분석만으로(임포트·DB·네트워크 없음) 찾는다:

  1. router_unregistered       -- src/api/routers/*의 APIRouter가 앱에 include 안 됨
  2. port_method_unimplemented -- ABC 포트 메서드가 서브클래스에 없거나
                                   raise NotImplementedError인데 ratchet-allow 없음
  3. env_key_undocumented      -- os.environ 접근 키가 .env.example에 없음
  4. feature_flag_undocumented -- flag_enabled(...) 이름이 .env.example에 없음
  5. event_type_unconsumed     -- publish()된 topic에 subscribe() 소비자가 없음
  6. migration_hygiene         -- downgrade()가 비어 있거나 head가 2개 이상
  7. openapi_client_mismatch   -- OpenAPI 경로 ↔ frontend apiRoutes 양방향 불일치
  8. spec_leaf_untraced        -- 명세 §9 리프 ID가 코드/커밋 어디에도 없음
  9. naive_datetime            -- datetime.now()/utcnow()가 tz 없이 호출됨
  10. money_float              -- 금액류 이름(amount/price/fee 등)이 float로 선언됨
  11. symbol_id_assembly       -- symbol/instrument_id를 f-string/concat/join으로 직접 조립
  12. spec_template_incomplete -- 명세 파일에 "## 9." 리프 목록 또는 미확정 절이 없음
  13. authority_duplication    -- 같은 bounded context에서 같은 "authority"(멱등키 등)를
                                   2개 이상의 파일이 각자 raw assembly로 재조립(RATCHET-2,
                                   task-3256)

`check_code_ratchets.py`와 같은 래칫 방식: `consistency-baseline.json`에 지표별
현재 위반 수를 기록하고, 이후 실행에서 그 수가 늘면 exit 2(적색). 각 검사는
과탐(false positive)을 baseline이 흡수하므로 완벽한 판정을 목표하지 않는다 --
"늘어나면 막는다"가 목표다.

사용: `python scripts/check_consistency.py [--update] [--root PATH] [--baseline PATH]`.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "consistency-baseline.json"

Hit = tuple[str, int]

_EXCLUDE_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
    }
)

_RATCHET_ALLOW_RE = re.compile(r"#\s*ratchet-allow:\s*(\S.*)")
_HEADER_SCAN_LINES = 20


# ---------------------------------------------------------------------------
# 공용 헬퍼
# ---------------------------------------------------------------------------


def _iter_py_files(root: Path, subdir: str) -> list[Path]:
    base = root / subdir
    if not base.is_dir():
        return []
    out = []
    for path in base.rglob("*.py"):
        if _EXCLUDE_DIR_NAMES & set(path.relative_to(root).parts[:-1]):
            continue
        out.append(path)
    return sorted(out)


def _safe_parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError:
        return None


def _ratchet_allow_reason(text: str) -> str | None:
    for line in text.splitlines()[:_HEADER_SCAN_LINES]:
        m = _RATCHET_ALLOW_RE.search(line)
        if m:
            return m.group(1).strip()
    return None


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _module_level_literal_consts(tree: ast.Module) -> dict[str, list[str]]:
    """모듈 최상단 `NAME = "x"` / `NAME = ("a","b")` 대입만 정적으로 읽는다."""
    consts: dict[str, list[str]] = {}
    for node in ast.iter_child_nodes(tree):
        target: str | None = None
        value: ast.expr | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            target, value = node.target.id, node.value
        if target is None or value is None:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            consts[target] = [value.value]
        elif isinstance(value, ast.Tuple | ast.List):
            items = [
                e.value
                for e in value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if items and len(items) == len(value.elts):
                consts[target] = items
    return consts


def _resolve_str(node: ast.expr, consts: dict[str, list[str]]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        vals = consts.get(node.id)
        if vals and len(vals) == 1:
            return vals[0]
    return None


def _resolve_seq(
    node: ast.expr, consts: dict[str, list[str]], scope: dict[str, list[str]]
) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Tuple | ast.List):
        items = [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        return items if items and len(items) == len(node.elts) else None
    if isinstance(node, ast.Name):
        if node.id in scope:
            return scope[node.id]
        return consts.get(node.id)
    return None


def _parse_env_example_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", stripped)
        if m:
            keys.add(m.group(1))
    return keys


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
        revision: str | None = None
        down_revision: str | None = None
        has_downgrade_body = False
        for node in ast.iter_child_nodes(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                name = node.targets[0].id
                if (
                    name == "revision"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    revision = node.value.value
                elif (
                    name == "down_revision"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    down_revision = node.value.value
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
                body = [s for s in node.body if not _is_docstring_stmt(s)]
                has_downgrade_body = any(
                    not isinstance(s, ast.Pass)
                    and not (
                        isinstance(s, ast.Expr)
                        and isinstance(s.value, ast.Constant)
                        and s.value.value is ...
                    )
                    for s in body
                )
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


# ---------------------------------------------------------------------------
# 8. spec_leaf_untraced
# ---------------------------------------------------------------------------

_LEAF_ROW_RE = re.compile(
    r"^\|\s*([A-Za-z]{1,6}-\d+[A-Za-z]?(?:~[A-Za-z]{1,6}-\d+[A-Za-z]?)?)\s*\|"
)
_LEAF_TOKEN_RE = re.compile(r"^([A-Za-z]+)-(\d+)([A-Za-z]?)$")


def _expand_leaf_ids(token: str) -> list[str]:
    if "~" not in token:
        return [token]
    left, right = token.split("~", 1)
    m1, m2 = _LEAF_TOKEN_RE.match(left), _LEAF_TOKEN_RE.match(right)
    if not (m1 and m2 and m1.group(1) == m2.group(1) and not m1.group(3) and not m2.group(3)):
        return [token]
    prefix, width = m1.group(1), len(m1.group(2))
    return [
        f"{prefix}-{str(n).zfill(width)}" for n in range(int(m1.group(2)), int(m2.group(2)) + 1)
    ]


def _collect_spec_leaf_ids(specs_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in sorted(specs_dir.glob("L4_*.md")):
        in_status_block = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("<!-- spec-status:begin"):
                in_status_block = True
                continue
            if stripped.startswith("<!-- spec-status:end"):
                in_status_block = False
                continue
            if in_status_block:
                continue
            m = _LEAF_ROW_RE.match(stripped)
            if m:
                ids.update(_expand_leaf_ids(m.group(1)))
    return ids


def _git_commit_subjects(root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "log", "--format=%s"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        return (r.stdout or "") if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _leaf_referenced(leaf: str, *blobs: str) -> bool:
    """leaf가 blob 안에 온전한 토큰으로 등장하는지 검사한다.

    plain substring(`leaf in blob`)은 AI-2가 AI-22/AI-20의 접두라서 그 커밋/코드만
    보고도 "추적됨"으로 오판한다 -- 양옆이 영숫자가 아닐 때만 일치로 센다.
    """
    pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(leaf) + r"(?![A-Za-z0-9])")
    return any(pattern.search(blob) for blob in blobs)


def check_spec_leaf_traceability(root: Path) -> list[Hit]:
    specs_dir = root / "docs" / "specs"
    if not specs_dir.is_dir():
        return []
    leaf_ids = _collect_spec_leaf_ids(specs_dir)
    if not leaf_ids:
        return []
    blobs = []
    for sub in ("src", "tests", "scripts"):
        base = root / sub
        if base.is_dir():
            for path in base.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                blobs.append(path.read_text(encoding="utf-8", errors="replace"))
    code_blob = "\n".join(blobs)
    commit_blob = _git_commit_subjects(root)
    return [
        (f"docs/specs#{leaf}", 0)
        for leaf in sorted(leaf_ids)
        if not _leaf_referenced(leaf, code_blob, commit_blob)
    ]


# ---------------------------------------------------------------------------
# 9. naive_datetime
# ---------------------------------------------------------------------------


def _dotted_chain(func: ast.expr) -> str:
    parts: list[str] = []
    cur = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def check_naive_datetime(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in _iter_py_files(root, "src"):
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            if attr not in ("now", "utcnow"):
                continue
            if "datetime" not in _dotted_chain(func).split("."):
                continue
            if attr == "utcnow":
                hits.append((rel, node.lineno))
                continue
            has_tz = bool(node.args) or any(kw.arg == "tz" for kw in node.keywords)
            if not has_tz:
                hits.append((rel, node.lineno))
    return hits


# ---------------------------------------------------------------------------
# 10. money_float
# ---------------------------------------------------------------------------

_MONEY_KEYWORDS = (
    "amount",
    "price",
    "balance",
    "fee",
    "cost",
    "notional",
    "cash",
    "pnl",
    "equity",
    "margin",
    "quantity",
    "qty",
    "proceeds",
    "payout",
    "principal",
)


def _is_money_name(name: str) -> bool:
    low = name.lower()
    return any(kw in low for kw in _MONEY_KEYWORDS)


def _is_float_annotation(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id == "float"
    if isinstance(node, ast.Subscript):
        return _is_float_annotation(node.slice)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_float_annotation(node.left) or _is_float_annotation(node.right)
    return False


def check_money_float(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in _iter_py_files(root, "src"):
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if _is_float_annotation(node.annotation) and _is_money_name(node.target.id):
                    hits.append((rel, node.lineno))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for arg in [*node.args.args, *node.args.kwonlyargs]:
                    if _is_float_annotation(arg.annotation) and _is_money_name(arg.arg):
                        hits.append((rel, node.lineno))
    return hits


# ---------------------------------------------------------------------------
# 11. symbol_id_assembly (FA-0d position_key 규칙 확장)
# ---------------------------------------------------------------------------

_ASSEMBLY_TARGET_NAMES = frozenset({"symbol", "instrument_id"})
_EXEMPT_DIR_PARTS = (("src", "db", "migrations"),)


def _is_assembly_expr(node: ast.expr) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return True
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
    )


def check_symbol_id_assembly(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in _iter_py_files(root, "src"):
        rel_parts = path.relative_to(root).parts
        if any(rel_parts[: len(prefix)] == prefix for prefix in _EXEMPT_DIR_PARTS):
            continue
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            lineno = 0
            if isinstance(node, ast.Assign):
                targets, value, lineno = node.targets, node.value, node.lineno
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value, lineno = [node.target], node.value, node.lineno
            if value is not None and _is_assembly_expr(value):
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in _ASSEMBLY_TARGET_NAMES:
                        hits.append((rel, lineno))
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in _ASSEMBLY_TARGET_NAMES and _is_assembly_expr(kw.value):
                        hits.append((rel, kw.value.lineno))
    return hits


# ---------------------------------------------------------------------------
# 13. authority_duplication (RATCHET-2, task-3256)
# ---------------------------------------------------------------------------

_AUTHORITY_TARGET_NAMES = frozenset({"idempotency_key"})


def _bounded_context(rel_parts: tuple[str, ...]) -> str | None:
    """`src/foundation/<agg>/...` -> `foundation.<agg>`, `src/core/<d>/...` -> `core.<d>`."""
    if len(rel_parts) < 3:
        return None
    if rel_parts[0] == "src" and rel_parts[1] in ("foundation", "core"):
        return f"{rel_parts[1]}.{rel_parts[2]}"
    return None


def check_authority_duplication(root: Path) -> list[Hit]:
    """같은 bounded context 안에서 같은 이름의 "authority"(멱등키 등)를 서로 다른
    파일이 각자 raw assembly(f-string/concat/join)로 재조립하면 위반 --
    `record_fill.py`가 `journal_rules.py`의 `fill:` 멱등키 스킴을 독립적으로
    재구성한 사례가 실제 동기다(FA 축, task-3256 조사). 캔노니컬 빌더 호출이나
    기존 값을 그대로 전달하는 것은 위반이 아니다 -- raw assembly만 잡는다
    (`check_position_key_central.py`와 같은 판정 원칙: 완벽한 판정을 목표하지
    않고, 늘어나는 것만 baseline으로 막는다)."""
    by_context_target: dict[tuple[str, str], list[Hit]] = {}
    for path in _iter_py_files(root, "src"):
        rel_parts = path.relative_to(root).parts
        if any(rel_parts[: len(prefix)] == prefix for prefix in _EXEMPT_DIR_PARTS):
            continue
        context = _bounded_context(rel_parts)
        if context is None:
            continue
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            lineno = 0
            if isinstance(node, ast.Assign):
                targets, value, lineno = node.targets, node.value, node.lineno
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value, lineno = [node.target], node.value, node.lineno
            if value is not None and _is_assembly_expr(value):
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in _AUTHORITY_TARGET_NAMES:
                        key = (context, target.id)
                        by_context_target.setdefault(key, []).append((rel, lineno))
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in _AUTHORITY_TARGET_NAMES and _is_assembly_expr(kw.value):
                        key = (context, kw.arg)
                        by_context_target.setdefault(key, []).append((rel, kw.value.lineno))

    hits: list[Hit] = []
    for sites in by_context_target.values():
        distinct_files = {rel for rel, _ in sites}
        if len(distinct_files) >= 2:
            hits.extend(sites)
    return hits


# ---------------------------------------------------------------------------
# 12. spec_template_incomplete
# ---------------------------------------------------------------------------

_LEAF_SECTION_RE = re.compile(r"^##\s*9\.", re.MULTILINE)
_OPEN_SECTION_RE = re.compile(r"^##\s*10\.", re.MULTILINE)


def check_spec_template(root: Path) -> list[Hit]:
    specs_dir = root / "docs" / "specs"
    if not specs_dir.is_dir():
        return []
    hits: list[Hit] = []
    for path in sorted(specs_dir.glob("L4_*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        if not _LEAF_SECTION_RE.search(text):
            hits.append((rel, 0))
        if not _OPEN_SECTION_RE.search(text) and "미확정" not in text:
            hits.append((rel, 0))
    return hits


# ---------------------------------------------------------------------------
# 집계 · 래칫 · CLI
# ---------------------------------------------------------------------------

METRICS: dict[str, Callable[[Path], list[Hit]]] = {
    "router_unregistered": check_router_wiring,
    "port_method_unimplemented": check_port_implementations,
    "env_key_undocumented": check_env_keys,
    "feature_flag_undocumented": check_feature_flags,
    "event_type_unconsumed": check_event_consumers,
    "migration_hygiene": check_migrations,
    "openapi_client_mismatch": check_openapi_frontend,
    "spec_leaf_untraced": check_spec_leaf_traceability,
    "naive_datetime": check_naive_datetime,
    "money_float": check_money_float,
    "symbol_id_assembly": check_symbol_id_assembly,
    "spec_template_incomplete": check_spec_template,
    "authority_duplication": check_authority_duplication,
}


class ConsistencyError(ValueError):
    """baseline JSON 형식 오류."""


def scan_all(root: Path) -> dict[str, list[Hit]]:
    return {name: sorted(fn(root)) for name, fn in METRICS.items()}


def counts_of(hits: dict[str, list[Hit]]) -> dict[str, int]:
    return {metric: len(hits[metric]) for metric in METRICS}


def read_baseline(path: Path) -> dict[str, int] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ConsistencyError(f"baseline 파일이 비어 있음: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConsistencyError(f"baseline JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise ConsistencyError("baseline JSON은 객체여야 함")
    result: dict[str, int] = {}
    for metric in METRICS:
        value = data.get(metric)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConsistencyError(f"baseline 값이 정수가 아님: {metric}={value!r}")
        result[metric] = value
    return result


def write_baseline(path: Path, counts: dict[str, int]) -> None:
    payload = {metric: counts[metric] for metric in METRICS}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="감소분을 baseline 파일에 반영")
    parser.add_argument("--top", type=int, default=10, help="증가한 지표별로 보여줄 목록 개수")
    args = parser.parse_args(argv)

    try:
        baseline = read_baseline(args.baseline)
    except ConsistencyError as exc:
        print(f"FAIL: {exc}")
        return 1

    hits = scan_all(args.root)
    current = counts_of(hits)

    if baseline is None:
        write_baseline(args.baseline, current)
        print(f"BASELINE 초기화: {current} -> {args.baseline}")
        return 0

    increased = {m: (baseline[m], current[m]) for m in METRICS if current[m] > baseline[m]}
    if increased:
        for metric, (before, after) in increased.items():
            print(f"FAIL: {metric} {before}개 -> {after}개 (증가)")
            for rel, lineno in hits[metric][: args.top]:
                print(f"    {rel}:{lineno}")
        return 2

    decreased = {m for m in METRICS if current[m] < baseline[m]}
    if decreased and args.update:
        write_baseline(args.baseline, current)
        print(f"OK: 감소, baseline 갱신 {baseline} -> {current}")
        return 0

    if decreased:
        print(f"OK: 감소했으나 baseline 유지(--update로 반영) {baseline} (현재 {current})")
        return 0

    print(f"OK: {current} (baseline {baseline})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
