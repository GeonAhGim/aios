"""PLT-16 — `check_openapi_compat.find_violations` + CLI: 수치성능단언·서브프로세스 경계.

DB·네트워크·앱 임포트 전부 없음(decision) — 베이스라인/현재 스키마는 이
파일에서 직접 만든 dict/fixture만 쓴다.

task-3940: test_openapi_compat.py(772줄) 분할 3/3 — DEEPEN(task-1956/3151)
성능·서브프로세스 관심사. 1/3은 test_openapi_compat_paths.py(path/method/CLI 기본),
2/3은 test_openapi_compat_schemas.py(정규화·중첩 $ref).
3/3은 이 파일(성능·서브프로세스)과 test_openapi_compat_actions.py(실패주입·게이트적색재현).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, cast

import pytest

import scripts.check_openapi_compat as compat_module
from scripts.check_openapi_compat import main
from tests.conftest import PerfBudget


def _schema(*, paths: dict[str, Any], schemas: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"paths": paths, "components": {"schemas": schemas or {}}}


def _api_response_envelope(
    data_ref: str, meta_ref: str = "#/components/schemas/Meta"
) -> dict[str, Any]:
    """ApiResponse_XXX_ 봉투 스키마를 만든다."""
    return {
        "type": "object",
        "properties": {
            "data": {"$ref": data_ref},
            "meta": {"$ref": meta_ref},
        },
    }


def _meta_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "as_of": {"type": "string", "format": "date-time"},
            "trace_id": {"type": "string"},
        },
    }


def _string_prop(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"type": "string", "format": "uuid"}
    base.update(overrides)
    return base


# ── DEEPEN 1956: 수치성능단언·서브프로세스 경계 ────────────────────────────


@pytest.mark.perf
def test_performance_assertion_nested_ref_recursion_budget(
    perf_budget: PerfBudget,
) -> None:
    """수치 성능 단언: 중첩 $ref 재귀가 100개 스키마에서도 1초 이내에 완료된다.

    PLT-16의 재귀 비교가 스키마 수가 많아져도 실용적인 시간 내에 종료되어야 한다.
    raw perf_counter() → perf_budget.assert_within(batch=20) 전환.
    """
    # 100개의 중첩 스키마 체인 생성
    schemas = {"Meta": _meta_schema()}
    paths = {}
    for i in range(100):
        ref_name = f"Level{i}"
        inner_ref = "#/components/schemas/Inner" if i == 0 else f"#/components/schemas/Level{i - 1}"
        schemas[ref_name] = {
            "type": "object",
            "properties": {
                "id": _string_prop(),
                "next": {"$ref": inner_ref},
            },
        }
    # 시작점
    paths["/chain"] = {
        "get": {
            "responses": {
                "200": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Level0"}}
                    }
                }
            }
        }
    }
    from scripts.check_openapi_compat import find_violations

    baseline = _schema(paths=paths, schemas=schemas)
    current = json.loads(json.dumps(baseline))

    sample = perf_budget.assert_within(
        lambda: find_violations(baseline, current),
        budget_ms=1000.0,
        batch=20,
    )

    # 동일 스키마이므로 위반이 없어야 함
    assert sample.result == [], f"동일 스키마에서 위반이 없어야 함: {sample.result}"


def test_performance_assertion_fails_with_cpu_delay_injection(
    monkeypatch: Any,
) -> None:
    """성능 단언이 실제로 예산을 검증하는지 확인한다 — 동일 호출에 지연을
    주입하면 PerfBudget.assert_within가 AssertionError를 던져야 한다
    (단언/마커 삭제나 예산 완화가 아닌 genuine budget check임 증명).

    time.process_time()을 monkeypatch해 매 호출 시 누적 50ms를 추가하면
    측정 구간(start→end)의 차이에 50ms가 더해져 budget_ms=10을 초과한다.
    """
    import time as _time

    from scripts.check_openapi_compat import find_violations
    from tests.support.perf_budget import PerfBudget as _PerfBudget

    original_process_time = _time.process_time
    _offset = [0.0]

    def _slow_process_time() -> float:
        t = original_process_time()
        _offset[0] += 0.05
        return t + _offset[0]

    # 100개의 중첩 스키마 체인 생성 (기존 테스트와 동일)
    schemas = {"Meta": _meta_schema()}
    paths: dict[str, Any] = {}
    for i in range(100):
        ref_name = f"Level{i}"
        inner_ref = "#/components/schemas/Inner" if i == 0 else f"#/components/schemas/Level{i - 1}"
        schemas[ref_name] = {
            "type": "object",
            "properties": {
                "id": _string_prop(),
                "next": {"$ref": inner_ref},
            },
        }
    paths["/chain"] = {
        "get": {
            "responses": {
                "200": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Level0"}}
                    }
                }
            }
        }
    }
    baseline = _schema(paths=paths, schemas=schemas)
    current = json.loads(json.dumps(baseline))

    monkeypatch.setattr(_time, "process_time", _slow_process_time)

    with pytest.raises(AssertionError):
        _PerfBudget.assert_within(
            _PerfBudget(),
            lambda: find_violations(baseline, current),
            budget_ms=10.0,
            batch=1,
        )


def test_main_raises_when_export_subprocess_fails(monkeypatch: Any, tmp_path: Any) -> None:
    """실패 주입: export_openapi 서브프로세스가 실패하면(main.py가 exit code를
    확인하고 그대로 전파한다)."""
    baseline = _schema(
        paths={
            "/widgets": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Widget"}
                                }
                            }
                        }
                    }
                }
            }
        },
        schemas={"Widget": {"type": "object", "properties": {"id": {"type": "string"}}}},
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    def _fail(*args: Any, **kwargs: Any) -> None:
        raise subprocess.CalledProcessError(returncode=1, cmd=["export_openapi"])

    monkeypatch.setattr(cast(Any, compat_module).subprocess, "run", _fail)

    with pytest.raises(subprocess.CalledProcessError):
        main(["--baseline", str(baseline_path)])


def test_main_propagates_when_export_subprocess_lies_about_success(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """실패 주입: 서브프로세스가 returncode 0으로 끝나도 출력 파일을 쓰지
    않으면(부분 실패·버그) 그 누락을 놓치고 통과시키면 안 된다."""
    baseline = _schema(
        paths={
            "/widgets": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Widget"}
                                }
                            }
                        }
                    }
                }
            }
        },
        schemas={"Widget": {"type": "object", "properties": {"id": {"type": "string"}}}},
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    def _noop_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=["export_openapi"], returncode=0)

    monkeypatch.setattr(cast(Any, compat_module).subprocess, "run", _noop_run)

    with pytest.raises(FileNotFoundError):
        main(["--baseline", str(baseline_path)])
