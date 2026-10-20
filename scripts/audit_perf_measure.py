"""Inventory completed PERF-measure conversions; never infer dynamic success from text.

Read-only with respect to the PM repository. Outputs review evidence and split requests.
This is an audit tool, not a newly wired CI gate.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from pathlib import Path


def _is_perf_test(node: ast.AST, source: str) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if not node.name.startswith("test_"):
        return False
    body = ast.get_source_segment(source, node) or ""
    return "perf_budget" in body or "assert_async_p99_ms" in body


def _is_single_call_perf(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in {"sample", "samples", "best_of", "sample_async", "samples_async"}:
        return False
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "perf_budget":
        return False
    batch = next((k.value for k in node.keywords if k.arg == "batch"), None)
    return batch is None or (isinstance(batch, ast.Constant) and batch.value == 1)


def _is_rescaled_cpu_ms(node: ast.AST) -> bool:
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Mult):
        return False
    for value, factor in ((node.left, node.right), (node.right, node.left)):
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "cpu_ms"
            and isinstance(factor, ast.Constant)
            and factor.value == 1000
        ):
            return True
    return False


def inspect_source(source: str) -> dict[str, object]:
    tree = ast.parse(source)
    single_calls = []
    rescaled = []
    tests = []
    for node in ast.walk(tree):
        if _is_perf_test(node, source):
            tests.append(node.name)
        if _is_single_call_perf(node):
            single_calls.append(node.lineno)
        if _is_rescaled_cpu_ms(node):
            rescaled.append(node.lineno)
    has_red = "gate_red" in source or "assert_slowdown_detected" in source
    findings = []
    if single_calls:
        findings.append("single-call API: inspect inner operation/batch and CPU clock quantization")
    if rescaled:
        findings.append("cpu_ms multiplied by 1000: verify comparison unit")
    if not has_red:
        findings.append("no named gate-red evidence")
    return {
        "tests": tests,
        "single_call_lines": single_calls,
        "rescaled_cpu_ms_lines": rescaled,
        "gate_red_named": has_red,
        "findings": findings,
        "zero_convergence": "not dynamically measured",
        "gate_red_result": "not executed; names do not prove detection",
        "units": "review rescaling evidence" if rescaled else "requires assertion-level review",
        "corrected": False,
    }


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8")


def _load_conversions(
    tasks_dir: Path,
) -> tuple[list[dict[str, object]], dict[str, list[dict[str, object]]]]:
    conversions = []
    files: dict[str, list[dict[str, object]]] = {}
    for path in sorted(tasks_dir.glob("task-*.json")):
        task = json.loads(path.read_text(encoding="utf-8-sig"))
        if task.get("status") != "done" or not task.get("title", "").startswith(
            "[health:ci_red] PERF-measure:"
        ):
            continue
        record = {key: task.get(key) for key in ("id", "commit", "files", "title")}
        conversions.append(record)
        for filename in task.get("files", []):
            files.setdefault(filename, []).append(record)
    return conversions, files


def _process_file_record(
    filename: str, records: list[dict[str, object]]
) -> tuple[dict[str, object], list[dict[str, object]]]:
    path = Path(filename)
    row: dict[str, object] = {"file": filename, "conversions": records}
    if path.is_file() and path.suffix == ".py":
        row.update(inspect_source(path.read_text(encoding="utf-8-sig")))
    else:
        row.update(findings=["file missing or not Python"], corrected=False)

    evidence = []
    for record in records:
        commit = record["commit"]
        if not commit:
            evidence.append({"commit": commit, "status": "missing commit"})
            continue
        try:
            diff = git("show", "--format=", str(commit), "--", filename)
            budget_lines = [
                line
                for line in diff.splitlines()
                if line.startswith(("+", "-"))
                and not line.startswith(("+++", "---"))
                and any(word in line.lower() for word in ("budget", "assert", "threshold"))
            ]
            evidence.append(
                {
                    "commit": commit,
                    "changed_assertion_lines": budget_lines,
                    "status": "review required"
                    if budget_lines
                    else "no budget/assertion line changed",
                }
            )
        except subprocess.CalledProcessError:
            evidence.append({"commit": commit, "status": "unresolvable commit"})
    row["budget_history"] = evidence
    return row, records


def _make_backend_request(filename: str) -> dict[str, object]:
    return {
        "role": "backend",
        "priority": 1,
        "tier": "L",
        "stage": "implement",
        "status": "assigned",
        "parent": 11539,
        "depends_on": [11539],
        "attempts": 0,
        "title": f"[health:ci_red] PERF-detection: {filename}",
        "files": [filename],
        "spec": "task-11539 감사 후속. "
        "docs/audits/task-11539-perf-measure.json의 "
        "해당 파일 증거를 검토한다. "
        "호출 1회 CPU 측정의 0 수렴과 단위/예산 "
        "불변을 검증하고 결함이면 batch로 정정한다. "
        "기존 실제 성능 단언을 정상 입력에서 녹색, 예산 "
        "10배 지연 monkeypatch에서 적색으로 검증한다. "
        "CPU 예산이면 CPU 부하를, 대기 포함 지연 "
        "예산이면 sleep을 주입하고 같은 측정/단언을 재사용한다. "
        "DoD: 음성 테스트 3개, 주입 실패, 성능 수치, gate-red 재현. "
        "단언/마커 삭제와 예산 완화 금지. 정적 후보는 확정 "
        "결함이 아니므로 내부 batch 여부를 먼저 확인한다.",
        "commit": "",
        "tests": "",
        "note": "task-11539 파일별 검출력 확인 분할",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    conversions, files = _load_conversions(args.tasks)
    rows = []
    requests = []
    for filename, records in sorted(files.items()):
        row, _ = _process_file_record(filename, records)
        rows.append(row)
        if filename != "tests/integration/api/test_rate_limit_storm.py":
            requests.append(_make_backend_request(filename))
    requests.append(
        {
            "role": "ops",
            "priority": 1,
            "tier": "L",
            "stage": "implement",
            "status": "assigned",
            "parent": 11539,
            "depends_on": [11539],
            "attempts": 0,
            "title": "[health:ci_red] PERF-measure 명세에 batch·단위·10배 주입 검증 의무화",
            "files": ["scripts/local_leaf_sources.py"],
            "commit": "",
            "tests": "",
            "note": "task-11539 PM 소유 코드 후속; 앱 워커 직접 편집 금지",
            "spec": "PM 저장소 scripts/local_leaf_sources.py의 "
            "PERF-measure 명세에 호출 1회 측정 금지(batch 필수), "
            "단위 명시, 동일한 실제 단언의 10배 지연 주입 검출력 확인을 추가한다. "
            "CPU 시간은 sleep을 검출하지 못하므로 측정 축과 주입 축을 일치시킨다. "
            "DoD: 명세 생성 회귀 테스트, 필수 문구 누락 음성 테스트, "
            "예산/마커 불변 확인. 새 CI gate 배선은 하지 않는다.",
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "head": git("rev-parse", "HEAD").strip(),
        "conversion_count": len(conversions),
        "file_count": len(rows),
        "scope": "all currently done tasks with the requested title prefix",
        "limitations": "static evidence is not dynamic certification; "
        "follow-up leaves must run the original assertion",
        "conversions": conversions,
        "files": rows,
        "split_requests": requests,
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# task-11539 PERF-measure 점검표",
        "",
        f"완료 전환 {len(conversions)}건 / 파일 {len(rows)}개.",
        "169건은 요청 당시 수치이며 현재 완료 목록 "
        "전체를 포함했다. 정적 후보와 확정 결함을 구분한다.",
        "단위·예산 변경 원문은 동명 JSON의 "
        "budget_history, 호출 위치는 single_call_lines에 보존했다.",
        "0 수렴/주입 적색은 동적 미검증이면 통과로 판정하지 않는다. 파일별 후속에서 확인한다.",
        "",
        "| 파일 | 단위 | batch 의심 위치 | 0 수렴 | gate-red | 예산 | 정정 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['file']} | {row.get('units', '미확인')} | "
            f"{row.get('single_call_lines', [])} | 미실측 | "
            f"{'이름 존재·실행 미확인' if row.get('gate_red_named') else '증거 없음'} | "
            "JSON diff·판정 미확정 | 후속 검증 |"
        )
    args.output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"conversions={len(conversions)} files={len(rows)} split_requests={len(requests)}")


if __name__ == "__main__":
    main()
