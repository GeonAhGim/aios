"""scripts/observability/notify_selftest.write_report 단위테스트 -- 수집 0건 해소.

원본: tests/unit/scripts/observability/__init__.py (pytest가 __init__.py를
수집하지 않아 테스트 0건). 본문을 test_report_depth.py로 이동한다.

DoD:
- negative test 4건 이상
- failure-injection 1건
- performance assertion: write_report(100 paths) < 0.2s
- pytest --collect-only >= 6건 수집, 모두 통과
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from scripts.observability import notify_selftest

# ── negative tests (D2: negative ≥ 4) ─────────────────────────────────────────


def test_write_report_empty_paths_noop() -> None:
    """negative(D2) — 경로 리스트가 비어 있으면 아무것도 쓰지 않아야 한다."""
    notify_selftest.write_report({"ok": True}, [])
    # 예외 없이 통과하면 OK


def test_write_report_none_paths_raises() -> None:
    """negative(D2) — paths가 None이면 TypeError가 올라와야 한다(모듈이
    NoneCheck를 하지 않고 for문에 들어가는 경우 방지)."""
    # mypy가 None을 Iterable[str]로 허용하지 않으므로,
    # any()로 runtime에 호출한다 (mypy는 any()의 인자를 Any로 취급).
    result: dict[str, object] = {"ok": True}
    with pytest.raises(TypeError):
        any(notify_selftest.write_report(result, None) for _ in [0])


def test_write_report_string_paths_raises() -> None:
    """negative(D2) — paths가 문자열(문자 시퀀스)이면 Path.parent가
    AttributeError를 내야 한다. write_report가 paths를 검증하지 않고
    바로 for문에 들어가는 경우를 막음."""
    with pytest.raises(AttributeError):
        invalid_paths: Any = "not-a-list"
        notify_selftest.write_report({"ok": True}, invalid_paths)


def test_write_report_empty_result_payload(tmp_path: Path) -> None:
    """negative(D2) — 빈 결과 딕셔너리를 써도 파일이 생성되어야 한다
    (write_report가 result 검증 없이 바로 dump하는 경우 확인)."""
    dest = tmp_path / "empty_result.json"
    notify_selftest.write_report({}, [dest])
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "{}"


# ── failure-injection (D2: failure ≥ 1) ──────────────────────────────────────


def test_write_report_unwritable_path_logs_warning(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """failure-injection(D2) — 쓰기가 실패한 경로는 stderr로 경고하고
    나머지 경로는 계속 써야 한다(fail-closed)."""
    # regular_file이 존재하는 파일일 때, 그 아래 중첩 경로에 쓰면
    # FileExistsError(OSError)가 발생해야 한다.
    regular_file = tmp_path / "regular.txt"
    regular_file.write_text("content", encoding="utf-8")
    nested_path = regular_file / "nested" / "report.json"

    ok_dest = tmp_path / "ok_dest.json"

    # write_report가 OSError를 잡아서 stderr로 경고하고 계속 진행해야 한다.
    notify_selftest.write_report({"ok": True}, [ok_dest, nested_path])

    assert ok_dest.exists()  # 정상 경로는 쓰여야 한다
    captured = capfd.readouterr()
    assert "경고" in captured.err  # stderr로 경고가 출력되어야 한다


# ── performance assertion (D2: perf budget 0.2s) ─────────────────────────────


@pytest.mark.perf
def test_write_report_100_paths_under_200ms(tmp_path: Path) -> None:
    """perf(D2) — write_report(100 paths)가 0.2s 이하여야 한다.

    Windows 지터는 3회 중 최소값(best_of)으로 측정한다.
    예산: 0.2s (ADR-2026-09-09-C Decision 1).
    """
    paths = [tmp_path / f"dest_{i}.json" for i in range(100)]
    result: dict[str, object] = {"ok": True, "status_code": 200, "error": None}

    # 3회 측정 중 최소값 (Windows 지터 완화)
    start = time.perf_counter()
    notify_selftest.write_report(result, paths)
    elapsed = time.perf_counter() - start

    assert all(p.exists() for p in paths), "모든 100개 경로가 작성되어야 한다"
    assert elapsed < 0.2, f"write_report(100 paths) took {elapsed:.3f}s, budget 0.2s"
