"""R-59(task-1750) — I-09 이중 권위 강제: 운영 조립 지점의 `require_mandate=True`
정적 단언.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-59,
docs/design/ADR-2026-09-06-G-second-audit-corrections.md §8 I-09 신설 근거.

배경: `make_foundation_pre_submit_gate(pool, require_mandate=...)`가
`False`로 조립되면 `foundation_gate.py`의 "mandate 없음" 분기가 DENY 대신
감사로그만 남기고 통과시킨다(`src/services/order_service/foundation_gate.py`
`if require_mandate:` 분기) — 이중 권위(RiskEngine ∩ Compliance)의 절반이
no-op이 되는 형태다. ADR-2026-09-06-G §8이 지적한 시점에는
`src/services/background_loops.py:252`가 실제로 `require_mandate=False`였다
(H-1b/task-3369가 이후 세 조립 지점 모두 `True`로 교정).

이 파일은 그 교정이 유지됨을 회귀 방지로 고정한다. 두 운영 조립 지점
(`src/services/background_loops.py`, `src/api/execution_deps.py`)의
`make_foundation_pre_submit_gate(...)` 호출부를 AST로 찾아 각각:
  (a) `require_mandate=True` 리터럴이거나,
  (b) 호출 직전 3줄 이내에 `ADR-EXCEPTION: <adr-id> require_mandate=False
      expires=YYYY-MM-DD` 주석이 있고 그 만료일이 아직 지나지 않았어야 한다.
둘 중 어느 쪽도 아니면(또는 예외가 만료됐으면) 위반이다 — "무기한 우회"를
"만료 추적되는 우회"로 바꾸는 것이 이 DoD의 핵심이다.
"""
from __future__ import annotations

import ast
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

_TARGET_FILES = (
    "src/services/background_loops.py",
    "src/api/execution_deps.py",
)

_GATE_FACTORY = "make_foundation_pre_submit_gate"

_ADR_EXCEPTION_RE = re.compile(
    r"ADR-EXCEPTION:\s*(?P<adr>[\w-]+)\s+require_mandate=False\s+expires=(?P<date>\d{4}-\d{2}-\d{2})"
)


@dataclass(frozen=True)
class Finding:
    location: str
    lineno: int
    ok: bool
    reason: str

    def __str__(self) -> str:
        return f"{self.location}:{self.lineno} — {self.reason}"


def _callee_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _find_gate_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee_name(node.func) == _GATE_FACTORY
    ]


def _require_mandate_arg(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "require_mandate":
            return kw.value
    return None


def _nearby_adr_exception(lines: list[str], lineno: int) -> tuple[str, date] | None:
    """`lineno`(1-indexed, `require_mandate=...` 키워드가 있는 줄) 및 그 앞
    3줄 안의 주석만 본다.

    범위를 좁게 잡는 이유: 파일 어딘가에 있는 무관한 ADR 예외 주석이 이
    호출의 예외로 오인되면(스코프 누수) 실제로 우회 사유가 없는 호출도
    통과시켜 버린다 — adversarial 테스트가 바로 이 스코프를 검증한다."""
    window = lines[max(0, lineno - 4) : lineno]
    for line in window:
        m = _ADR_EXCEPTION_RE.search(line)
        if m:
            return m.group("adr"), date.fromisoformat(m.group("date"))
    return None


def _scan_source(source: str, location: str, *, today: date) -> list[Finding]:
    tree = ast.parse(source)
    lines = source.splitlines()
    calls = _find_gate_calls(tree)
    if not calls:
        return [
            Finding(
                location,
                0,
                False,
                f"{_GATE_FACTORY} 호출을 찾지 못함 — 조립 지점이 이동/삭제됨"
                "(스캐너 대상 갱신 필요)",
            )
        ]
    findings: list[Finding] = []
    for call in calls:
        arg = _require_mandate_arg(call)
        if isinstance(arg, ast.Constant) and arg.value is True:
            findings.append(Finding(location, call.lineno, True, "require_mandate=True"))
            continue
        # 예외 주석은 `require_mandate=...` 키워드 자체의 줄(멀티라인 호출이면
        # 호출 시작 줄과 다르다) 바로 앞에서 찾는다 — 호출 시작 줄을 기준으로
        # 삼으면 여러 줄로 감싼 호출에서 키워드 바로 위 주석을 놓친다.
        anchor = arg.lineno if arg is not None else call.lineno
        exception = _nearby_adr_exception(lines, anchor)
        if exception is not None:
            adr, expires = exception
            if expires >= today:
                findings.append(
                    Finding(location, call.lineno, True, f"ADR 예외 {adr} 유효(만료 {expires})")
                )
            else:
                findings.append(
                    Finding(
                        location,
                        call.lineno,
                        False,
                        f"ADR 예외 {adr} 만료됨({expires}) — require_mandate=True로 복귀 필요",
                    )
                )
            continue
        findings.append(
            Finding(
                location,
                call.lineno,
                False,
                "require_mandate=True 아님, 유효한 ADR 예외도 없음 — I-09 이중 권위 우회",
            )
        )
    return findings


def _scan_file(path: Path, *, today: date) -> list[Finding]:
    source = path.read_text(encoding="utf-8")
    try:
        rel = path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        rel = str(path)
    return _scan_source(source, rel, today=today)


def _scan_violations(*, today: date) -> list[Finding]:
    findings: list[Finding] = []
    for rel in _TARGET_FILES:
        findings.extend(_scan_file(_REPO_ROOT / rel, today=today))
    return [f for f in findings if not f.ok]


# --- 스캐너 자체의 정확성 검증 (negative test 포함) -------------------------


def test_scanner_accepts_require_mandate_true():
    source = "make_foundation_pre_submit_gate(pool, require_mandate=True)\n"
    violations = [
        f for f in _scan_source(source, "fixture.py", today=date(2026, 1, 1)) if not f.ok
    ]
    assert violations == []


def test_scanner_flags_require_mandate_false_without_exception():
    """negative 1 — 예외 주석 없이 `require_mandate=False`면 위반이다."""
    source = "make_foundation_pre_submit_gate(pool, require_mandate=False)\n"
    findings = _scan_source(source, "fixture.py", today=date(2026, 1, 1))
    assert [str(f) for f in findings] == [
        "fixture.py:1 — require_mandate=True 아님, 유효한 ADR 예외도 없음 — I-09 이중 권위 우회"
    ]


def test_scanner_flags_missing_gate_call_entirely():
    """negative 2 — 조립 지점 자체가 사라지면(리팩터로 호출부 삭제/이동) 조용히
    "위반 0건"을 내는 대신 시끄럽게 실패해야 한다 — fail-open의 또 다른 형태다."""
    source = "def unrelated() -> None:\n    pass\n"
    findings = _scan_source(source, "fixture.py", today=date(2026, 1, 1))
    assert len(findings) == 1
    assert not findings[0].ok
    assert "호출을 찾지 못함" in findings[0].reason


def test_scanner_accepts_unexpired_adr_exception():
    source = (
        "gate = make_foundation_pre_submit_gate(\n"
        "    pool,\n"
        "    # ADR-EXCEPTION: ADR-2099-01-01-Z require_mandate=False expires=2099-01-01\n"
        "    require_mandate=False,\n"
        ")\n"
    )
    violations = [
        f for f in _scan_source(source, "fixture.py", today=date(2026, 1, 1)) if not f.ok
    ]
    assert violations == []


def test_scanner_rejects_expired_adr_exception():
    """negative 3 — 만료일이 지난 ADR 예외는 더 이상 우회 근거가 아니다. 이게
    바로 DoD의 핵심: "무기한 우회"가 아니라 "만료 추적되는 우회"여야 한다."""
    source = (
        "gate = make_foundation_pre_submit_gate(\n"
        "    pool,\n"
        "    # ADR-EXCEPTION: ADR-2020-01-01-Z require_mandate=False expires=2020-01-01\n"
        "    require_mandate=False,\n"
        ")\n"
    )
    findings = _scan_source(source, "fixture.py", today=date(2026, 1, 1))
    violations = [f for f in findings if not f.ok]
    assert len(violations) == 1
    assert "만료됨" in violations[0].reason


def test_scanner_adr_exception_does_not_leak_to_unrelated_later_call():
    """adversarial — ADR 예외 주석이 앞선 호출 바로 위에 있을 때, 그 주석이
    파일 뒤쪽의 **무관한** 다른 `require_mandate=False` 호출까지 정당화해
    주면 안 된다(스코프 누수 = 사실상 전역 우회 면허)."""
    source = (
        "# ADR-EXCEPTION: ADR-2099-01-01-Z require_mandate=False expires=2099-01-01\n"
        "gate_a = make_foundation_pre_submit_gate(pool_a, require_mandate=False)\n"
        "\n\n\n\n\n"
        "gate_b = make_foundation_pre_submit_gate(pool_b, require_mandate=False)\n"
    )
    findings = _scan_source(source, "fixture.py", today=date(2026, 1, 1))
    violations = [f for f in findings if not f.ok]
    assert len(violations) == 1
    assert violations[0].lineno == 8


# --- 회귀 방지 증명: task-1715류 원 결함 형태(게이트 적색 재현) ---------------


def test_regression_flags_original_i09_bug_shape():
    """ADR-2026-09-06-G §8이 지적한 시점의 실제 형태 —
    `background_loops.py:252`가 `require_mandate=False`로 감싸 조립했다.
    H-1b(task-3369)가 고치지 않았다면 이 스캐너가 지금도 잡아야 함을
    증명한다(게이트 적색 재현)."""
    source = (
        "pre_submit_gate=make_recovery_gate(\n"
        "    recovery_state, make_foundation_pre_submit_gate(pool, require_mandate=False)\n"
        "),\n"
    )
    findings = _scan_source(source, "background_loops.py", today=date(2026, 1, 1))
    violations = [f for f in findings if not f.ok]
    assert len(violations) == 1
    assert "이중 권위 우회" in violations[0].reason


# --- 실제 배선 코드 검사(하드 게이트) ----------------------------------------


def test_prod_assembly_points_require_mandate_true_or_valid_adr_exception():
    """하드 게이트 — xfail 없음. 두 운영 조립 지점 중 하나라도
    `require_mandate=True`가 아니고 유효한 ADR 예외도 없으면 CI가 빨간불이
    된다(R-59 DoD)."""
    violations = _scan_violations(today=date.today())
    assert violations == [], "\n".join(str(v) for v in violations)


# --- 실패 주입: I/O 실패가 조용히 fail-open으로 넘어가지 않는지 -------------


def test_scan_file_fails_closed_when_target_file_vanishes(tmp_path):
    """실패 주입 — 스캔 도중 대상 파일이 사라지면(파일시스템 경합) 예외로
    시끄럽게 실패해야 한다. 조용히 "위반 0건"으로 넘어가면 그 자체가
    fail-open이다."""
    vanished = tmp_path / "vanished.py"
    with pytest.raises(FileNotFoundError):
        _scan_file(vanished, today=date.today())


def test_scan_file_fails_closed_on_undecodable_file(tmp_path):
    """실패 주입 2 — 대상 파일이 UTF-8로 디코딩되지 않으면(손상된 배포본 등)
    역시 예외로 실패해야 한다. `errors="ignore"` 같은 관용적 디코딩으로 조용히
    넘어가면 손상된 파일의 실제 내용을 검사하지 못한 채 초록불을 낼 수 있다."""
    undecodable = tmp_path / "undecodable.py"
    undecodable.write_bytes(b"\xff\xfe\x00\x01garbage-not-utf8")
    with pytest.raises(UnicodeDecodeError):
        _scan_file(undecodable, today=date.today())


# --- 성능 단언 ---------------------------------------------------------------


def test_scan_source_perf_bound_for_large_synthetic_file():
    """성능 단언 — 3000개의 무관한 호출 사이에 대상 호출 1개를 섞은 합성
    대형 소스에서도 스캔이 선형 시간 안에 끝나야 한다. `_scan_source`가
    파일 전체를 `ast.walk`로 훑는데, 회귀로 줄 단위 재스캔(이차식)이
    들어와도 CI가 눈치채지 못하면 안 된다."""
    noise = "\n".join(f"unrelated_call_{i}(x, y, z)" for i in range(3000))
    source = f"{noise}\nmake_foundation_pre_submit_gate(pool, require_mandate=True)\n"
    start = time.perf_counter()
    findings = _scan_source(source, "fixture.py", today=date(2026, 1, 1))
    elapsed = time.perf_counter() - start
    assert [f.ok for f in findings] == [True]
    assert elapsed < 2.0, f"3000줄 잡음 속 1건 스캔에 {elapsed:.2f}s — 성능 회귀 의심"


# --- 다중 인스턴스(동시 실행) 증명 -------------------------------------------


def test_scan_source_consistent_across_concurrent_instances():
    """다중 인스턴스 증거 — CI가 여러 워커/스레드에서 동시에 이 스캐너를
    돌릴 수 있다. 모듈 전역 가변 상태가 없으므로 동시 실행에서도 항상 같은
    결과가 나와야 한다(경합으로 위반을 놓치는 거짓 초록불 방지)."""
    source = "make_foundation_pre_submit_gate(pool, require_mandate=False)\n"
    expected = [
        "fixture.py:1 — require_mandate=True 아님, 유효한 ADR 예외도 없음 — I-09 이중 권위 우회"
    ]
    today = date(2026, 1, 1)

    def _run(_: int) -> list[str]:
        return [str(f) for f in _scan_source(source, "fixture.py", today=today)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_run, range(16)))
    assert all(r == expected for r in results)
