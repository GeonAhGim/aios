"""OpenAPI 스냅샷 호환성 검사 — PLT-16(107 §3.3의 기계 판정).

`contracts/openapi/v1.json`(베이스라인)과 "현재" 스키마를 순수 JSON 구조로만
비교한다. 이 모듈은 FastAPI 앱을 임포트하지 않는다 — `--current`를 안 주면
`export_openapi.py`를 서브프로세스로 실행해 현재 스키마를 얻는다(앱 임포트는
그쪽에서만 발생). 이 스크립트는 절대 `contracts/openapi/v1.json`을 덮어쓰지
않는다 — 재생성은 `export_openapi.py`의 몫이고, 이 스크립트는 항상 "검사만"
한다(=`--check`가 유일한 동작).

판정 규칙(스펙 §8 표):
  - path·method 제거                                       → FAIL
  - 응답 property 제거 / type·format 변경 / nullable 축소   → FAIL
  - 요청 required 추가 / 요청 property 제거 / 요청 enum 제거 → FAIL
  - 응답 enum 값 제거                                        → FAIL
  - property·enum 값 "추가"는 전부 MINOR → 통과

PLT-16 정규화(비교 전 전처리): pydantic `Optional[X]`는 OpenAPI 3.1에서
`{"anyOf": [{...X}, {"type": "null"}]}`로 나타나 type·nullable 변경이 anyOf
안에 숨는다. `_normalize_node`가 이 패턴을 `(type=X, nullable=True)` 한
쌍으로 접어 기존 type/format/nullable/enum 규칙에 태운다. `$ref`·배열
`items` 안에 중첩된 anyOf도 재귀적으로 접는다(anyOf 항목 순서는 무시).

PLT-16 후속(`$ref`/배열 items 재귀): 프로퍼티가 평범한 `$ref`로 다른 객체
스키마를 가리키는 경우(예: `ApiResponse_X_.data` 봉투) `_property_violations`
는 원래 그 안쪽 프로퍼티를 보지 않았다. `_nested_object_violations`가
`$ref`·(`$ref`를 가리키는) 배열 items를 한 단계씩 더 해석해 안쪽 프로퍼티도
재귀 비교한다. `$ref` 순환참조는 방문한 컴포넌트 이름 집합(`visited`)으로
막는다 — 컴포넌트 이름 수는 유한하므로 같은 이름을 두 번 방문하기 전에
재귀가 끝난다.

사용: `python scripts/check_openapi_compat.py [--baseline PATH] [--current PATH]`.
종료코드 0=통과, 1=MAJOR 위반.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "contracts" / "openapi" / "v1.json"
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _resolve(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """`$ref: '#/components/schemas/X'` 1단계만 해석한다(이 저장소의 pydantic
    모델은 평평해서 대부분 충분하다 — 깊은 중첩은 이 리프 범위 밖)."""
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        resolved: dict[str, Any] = components.get(name, {})
        return resolved
    return schema


def _content_schema(
    content: dict[str, Any], components: dict[str, Any]
) -> dict[str, Any] | None:
    media = content.get("application/json")
    if media is None:
        return None
    return _resolve(media.get("schema", {}), components)


def _normalize_node(node: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """pydantic `Optional[X]`의 OpenAPI 3.1 표현(`anyOf: [X, {type: null}]`)을
    `(type=X..., nullable=True)` 한 쌍으로 접는다. `$ref`는 1단계 해석하고,
    배열 `items`·`$ref` 안에 중첩된 anyOf도 재귀적으로 접는다."""
    if not isinstance(node, dict):
        return node
    resolved = _resolve(node, components)
    any_of = resolved.get("anyOf")
    if isinstance(any_of, list):
        branches = [_normalize_node(b, components) for b in any_of]
        null_branches = [b for b in branches if isinstance(b, dict) and b.get("type") == "null"]
        other_branches = [
            b for b in branches if not (isinstance(b, dict) and b.get("type") == "null")
        ]
        if null_branches and len(other_branches) == 1:
            collapsed = dict(other_branches[0])
            collapsed["nullable"] = True
            return collapsed
        return resolved  # Optional 패턴이 아닌 anyOf(예: 3항 이상)는 그대로 둔다 — 범위 밖
    if resolved.get("type") == "array" and isinstance(resolved.get("items"), dict):
        result = dict(resolved)
        result["items"] = _normalize_node(resolved["items"], components)
        return result
    return resolved


def _leaf_violations(
    old_leaf: dict[str, Any], new_leaf: dict[str, Any], *, where: str, label: str
) -> list[str]:
    violations: list[str] = []
    old_type = old_leaf.get("type")
    if old_type is not None and old_type != new_leaf.get("type"):
        violations.append(
            f"{where} property type 변경: {label} ({old_type} -> {new_leaf.get('type')})"
        )
    old_format = old_leaf.get("format")
    if old_format is not None and old_format != new_leaf.get("format"):
        violations.append(f"{where} property format 변경: {label}")
    if old_leaf.get("nullable") is True and new_leaf.get("nullable") is not True:
        violations.append(f"{where} property nullable 축소: {label}")
    removed_enum = set(old_leaf.get("enum") or []) - set(new_leaf.get("enum") or [])
    if removed_enum:
        violations.append(f"{where} enum 값 제거: {label} {sorted(removed_enum, key=str)}")

    if old_type == "array" and isinstance(old_leaf.get("items"), dict):
        new_items = new_leaf.get("items")
        violations.extend(
            _leaf_violations(
                old_leaf["items"],
                new_items if isinstance(new_items, dict) else {},
                where=where,
                label=f"{label}[]",
            )
        )
    return violations


def _ref_target(
    node: Any, components: dict[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    """`$ref` 1단계 해석 + Optional(anyOf) 벗기기를 마친 노드를 반환한다.

    반환된 `ref_name`은 `$ref`가 직접 가리킨 컴포넌트 이름(순환 가드용 방문집합
    키)이다. anyOf(Optional) 안에 `$ref`가 있는 경우도 벗겨서 찾는다.
    """
    if not isinstance(node, dict):
        return None, {}
    ref = node.get("$ref")
    ref_name = ref.rsplit("/", 1)[-1] if isinstance(ref, str) else None
    resolved = _resolve(node, components)
    any_of = resolved.get("anyOf")
    if isinstance(any_of, list):
        others = [b for b in any_of if not (isinstance(b, dict) and b.get("type") == "null")]
        if len(others) == 1:
            return _ref_target(others[0], components)
        return None, {}
    return ref_name, resolved


def _nested_object_violations(
    old_prop_raw: Any,
    new_prop_raw: Any,
    *,
    where: str,
    label: str,
    components_old: dict[str, Any],
    components_new: dict[str, Any],
    visited: frozenset[str],
) -> list[str]:
    """PLT-16 후속: 프로퍼티가 (배열의) `$ref`로 다른 객체 스키마를 가리키면
    그 안쪽 프로퍼티까지 재귀 비교한다. `$ref` 순환참조는 방문집합으로 막는다
    (컴포넌트 이름은 유한하므로 방문집합만으로 종료가 보장된다)."""
    old_ref, old_target = _ref_target(old_prop_raw, components_old)
    _, new_target = _ref_target(new_prop_raw, components_new)

    if old_target.get("type") == "array" and isinstance(old_target.get("items"), dict):
        old_ref, old_target = _ref_target(old_target["items"], components_old)
        new_items = new_target.get("items")
        new_items = new_items if isinstance(new_items, dict) else {}
        _, new_target = _ref_target(new_items, components_new)
        label = f"{label}[]"

    if not isinstance(old_target.get("properties"), dict):
        return []
    if old_ref is not None:
        if old_ref in visited:
            return []
        visited = visited | {old_ref}

    return _property_violations(
        old_target,
        new_target,
        where=where,
        label=label,
        components_old=components_old,
        components_new=components_new,
        visited=visited,
    )


def _property_violations(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    where: str,
    label: str,
    components_old: dict[str, Any],
    components_new: dict[str, Any],
    visited: frozenset[str] = frozenset(),
) -> list[str]:
    violations: list[str] = []
    old_props: dict[str, Any] = old.get("properties", {})
    new_props: dict[str, Any] = new.get("properties", {})

    for name, old_prop_raw in old_props.items():
        new_prop_raw = new_props.get(name)
        if new_prop_raw is None:
            violations.append(f"{where} property 제거: {label} .{name}")
            continue
        old_leaf = _normalize_node(old_prop_raw, components_old)
        new_leaf = _normalize_node(new_prop_raw, components_new)
        violations.extend(
            _leaf_violations(old_leaf, new_leaf, where=where, label=f"{label} .{name}")
        )
        violations.extend(
            _nested_object_violations(
                old_prop_raw,
                new_prop_raw,
                where=where,
                label=f"{label} .{name}",
                components_old=components_old,
                components_new=components_new,
                visited=visited,
            )
        )

    if where == "request":
        added_required = set(new.get("required") or []) - set(old.get("required") or [])
        if added_required:
            violations.append(f"request required 추가: {label} {sorted(added_required)}")

    return violations


def find_violations(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """베이스라인 대비 MAJOR 위반 목록을 반환한다. 빈 목록이면 호환."""
    components_old: dict[str, Any] = (baseline.get("components") or {}).get("schemas", {})
    components_new: dict[str, Any] = (current.get("components") or {}).get("schemas", {})
    old_paths: dict[str, Any] = baseline.get("paths", {})
    new_paths: dict[str, Any] = current.get("paths", {})

    violations: list[str] = []
    for path, old_methods in old_paths.items():
        new_methods = new_paths.get(path)
        if new_methods is None:
            violations.append(f"path 제거: {path}")
            continue
        for method, old_op in old_methods.items():
            if method not in HTTP_METHODS:
                continue
            new_op = new_methods.get(method)
            if new_op is None:
                violations.append(f"method 제거: {method.upper()} {path}")
                continue

            old_request = _content_schema(
                (old_op.get("requestBody") or {}).get("content", {}), components_old
            )
            if old_request is not None:
                new_request = _content_schema(
                    (new_op.get("requestBody") or {}).get("content", {}), components_new
                )
                if new_request is None:
                    violations.append(f"request body 제거: {method.upper()} {path}")
                else:
                    violations.extend(
                        _property_violations(
                            old_request,
                            new_request,
                            where="request",
                            label=f"{method.upper()} {path} request",
                            components_old=components_old,
                            components_new=components_new,
                        )
                    )

            old_responses: dict[str, Any] = old_op.get("responses", {})
            new_responses: dict[str, Any] = new_op.get("responses", {})
            for status, old_resp in old_responses.items():
                new_resp = new_responses.get(status)
                if new_resp is None:
                    violations.append(f"response 제거: {method.upper()} {path} {status}")
                    continue
                old_schema = _content_schema(old_resp.get("content", {}), components_old)
                if old_schema is None:
                    continue
                new_schema = _content_schema(new_resp.get("content", {}), components_new)
                if new_schema is None:
                    violations.append(
                        f"response body 제거: {method.upper()} {path} {status}"
                    )
                    continue
                violations.extend(
                    _property_violations(
                        old_schema,
                        new_schema,
                        where="response",
                        label=f"{method.upper()} {path} {status} response",
                        components_old=components_old,
                        components_new=components_new,
                    )
                )
    return violations


def _export_current(out_dir: Path) -> dict[str, Any]:
    out_path = out_dir / "current-openapi.json"
    # 모듈 실행(`-m`)이어야 스크립트 자체 경로(`scripts/`)가 아니라 저장소
    # 루트(cwd=ROOT)가 sys.path에 실려 `src.main`을 임포트할 수 있다 —
    # `python scripts/export_openapi.py` 직접 실행은 `ModuleNotFoundError: src`.
    subprocess.run(
        [sys.executable, "-m", "scripts.export_openapi", "--out", str(out_path)],
        check=True,
        cwd=ROOT,
    )
    return _load(out_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI 스냅샷 호환성 검사(PLT-16)")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--current",
        type=Path,
        default=None,
        help="생략하면 export_openapi.py를 실행해 현재 앱 스키마를 즉시 생성",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지

    if not args.baseline.exists():
        print(f"FAIL: 베이스라인 스냅샷 없음 — {args.baseline}")
        return 1
    baseline = _load(args.baseline)

    if args.current is not None:
        current = _load(args.current)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            current = _export_current(Path(tmp))

    violations = find_violations(baseline, current)
    if violations:
        print(f"FAIL: OpenAPI 호환성 검사 실패 — {len(violations)}건")
        for violation in violations:
            print(f"  - {violation}")
        return 1

    print(f"OK: OpenAPI 호환성 검사 통과 — {args.baseline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
