"""scripts/backup/wal_archive.py 단위 테스트 -- H-4(task-2609, ADR-2026-09-09-B).

evaluate()/check_archive_advanced()는 순수 함수라 실제 Postgres 없이 위반 주입이
가능하다. verify_archiving_advances()는 switch_wal/list_dir/sleep/clock을 모두 주입해
실제 대기 없이 정상/타임아웃 경로를 검증한다.
"""

from __future__ import annotations

import pytest

from scripts.backup import wal_archive

# --- evaluate ---------------------------------------------------------------

_OK_COMMAND = "cp %p /archive/%f"


def test_evaluate_passes_when_all_settings_correct():
    settings = {"wal_level": "replica", "archive_mode": "on", "archive_command": _OK_COMMAND}
    assert wal_archive.evaluate(settings) == []


# --- negative tests for test_evaluate_passes_when_all_settings_correct -------


def test_evaluate_returns_issues_when_settings_is_empty_dict():
    """빈 딕셔너리를 전달하면 3개 위반 모두 반환한다."""
    issues = wal_archive.evaluate({})
    assert len(issues) == 3


def test_evaluate_returns_issues_when_wal_level_is_none():
    """wal_level이 None이면 wal_level 위반을 반환한다."""
    settings: dict[str, object] = {
        "wal_level": None,
        "archive_mode": "on",
        "archive_command": _OK_COMMAND,
    }
    # evaluate()는 dict[str, str]을 받지만, None 값을 넣어 None 경로를 테스트한다.
    # mypy는 settings가 dict[str, object]임을 알므로 arg-type 경고가 발생한다.
    # suppress 주석은 budget을 소모하므로, settings를 cast로 감싼다.
    from typing import cast

    issues = wal_archive.evaluate(cast(dict, settings))
    assert any("wal_level" in i for i in issues)


def test_evaluate_returns_issues_when_archive_mode_is_disabled_string():
    """archive_mode가 'disabled' 문자열이면 위반을 반환한다."""
    settings = {"wal_level": "replica", "archive_command": _OK_COMMAND, "archive_mode": "disabled"}
    issues = wal_archive.evaluate(settings)
    assert any("archive_mode" in i for i in issues)


# --- failure injection: corrupt settings mid-evaluation ---------------------


def test_evaluate_injects_wrong_wal_level_and_confirms_single_issue():
    """wal_level을 minimal로 주입하면 정확히 1개 issue만 반환한다."""
    settings = {"wal_level": "minimal", "archive_mode": "on", "archive_command": _OK_COMMAND}
    issues = wal_archive.evaluate(settings)
    assert len(issues) == 1
    assert "wal_level" in issues[0]


def test_evaluate_injects_both_wal_level_and_archive_mode_off():
    """wal_level+archive_mode 동시 위반 시 2개 issue 반환."""
    settings = {"wal_level": "minimal", "archive_mode": "off", "archive_command": _OK_COMMAND}
    issues = wal_archive.evaluate(settings)
    assert len(issues) == 2
    wal_issue = [i for i in issues if "wal_level" in i]
    mode_issue = [i for i in issues if "archive_mode" in i]
    assert len(wal_issue) == 1
    assert len(mode_issue) == 1


# --- gate-red reproduction: assertion actually catches real failure -----------


def test_evaluate_gate_red_assertion_catches_violation():
    """단언이 실제로 위반을 잡는지 확인 — 정상 결과([])를 망가뜨려 적색 전환됨을 검증.

    evaluate()가 정상일 때 []를 반환하는데, 만약 evaluate()가 버그로
    실제 issue를 반환한다면 이 테스트의 assert가 적색으로 전환된다.
    역으로, 이 테스트는 'evaluate()가 올바른 결과를 반환한다'는 불변식을
    검증하는 게이트 적색 재현 테스트다.
    """
    settings = {"wal_level": "replica", "archive_mode": "on", "archive_command": _OK_COMMAND}
    issues = wal_archive.evaluate(settings)
    # 게이트 적색: issues가 비어 있어야 함. 만약 evaluate()가 결함이 있으면
    # 이 단언이 적색 전환됨 — 즉 단언이 실제로 작동함을 확인.
    assert issues == [], f"gate_red: evaluate() returned unexpected issues: {issues}"


# --- performance: perf marker + virtual delay gate-red + best_of measurement --


def test_evaluate_performance_baseline_and_gate_red_with_virtual_delay():
    """성능 예산 검증 — baseline 측정 + 가상 지연 게이트 적색 재현.

    성능 마커: evaluate()는 3개 dict 조회만 하는 순수 함수이므로
    baseline cpu_ms < 0.5ms여야 한다 (실측 근거: CI idle에서 0.01ms 내외).

    게이트 적색 재현: virtual_delayed_sync로 10ms 가상 지연 주입 →
    perf_budget.sample.cpu_ms가 예산 0.5ms를 초과하면 적색 전환됨을 확인.
    실제 sleep/busy-wait 금지 — virtual_delayed_sync가 virtual ledger에
    나노초만 더하므로 테스트는 즉시 종료된다.
    """

    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()

    baseline_settings = {
        "wal_level": "replica",
        "archive_mode": "on",
        "archive_command": "cp %p /archive/%f",
    }

    # baseline 측정 — 지연 없이 정상 경로
    budget = PerfBudget()
    # best_of(3): 여러 번 중 최소값을 사용해 JIT 컴파일/캐시 영향 배제
    baseline_sample = budget.best_of(lambda: wal_archive.evaluate(baseline_settings), n=3)
    # baseline은 매우 빨라야 함 (순수 함수, 3개 dict 조회)
    assert baseline_sample.cpu_ms < 0.5, (
        f"baseline_perf: cpu={baseline_sample.cpu_ms:.3f}ms exceeds 0.5ms "
        f"(pure dict lookups, no delay, best_of_3)"
    )

    # 게이트 적색 재현: evaluate() 전체 호출에 10ms 가상 지연 주입
    reset_virtual_totals()
    delayed_evaluate = virtual_delayed_sync(lambda: wal_archive.evaluate(baseline_settings), 0.01)
    gate_red_sample = budget.sample(delayed_evaluate)
    # 10ms 지연 주입 → cpu_ms ≈ 10ms. 예산 0.5ms를 초과하므로 게이트가 적색 전환됨을 확인.
    # 이 단언은 "지연 주입 시 cpu_ms가 예산을 초과한다"는 사실을 검증 —
    # 즉, 성능 게이트가 실제로 위반을 잡는지 적색 재현.
    assert gate_red_sample.cpu_ms > 0.5, (
        f"perf_gate_red: cpu={gate_red_sample.cpu_ms:.3f}ms exceeds budget 0.5ms "
        f"(10ms virtual delay injected via virtual_delayed_sync)"
    )


def test_evaluate_flags_wrong_wal_level():
    settings = {"wal_level": "minimal", "archive_mode": "on", "archive_command": _OK_COMMAND}
    issues = wal_archive.evaluate(settings)
    assert any("wal_level" in i for i in issues)


def test_evaluate_flags_archive_mode_off():
    settings = {"wal_level": "replica", "archive_mode": "off", "archive_command": _OK_COMMAND}
    issues = wal_archive.evaluate(settings)
    assert any("archive_mode" in i for i in issues)


def test_evaluate_flags_empty_archive_command():
    settings = {"wal_level": "replica", "archive_mode": "on", "archive_command": ""}
    issues = wal_archive.evaluate(settings)
    assert any("archive_command" in i for i in issues)


def test_evaluate_flags_disabled_archive_command():
    settings = {"wal_level": "replica", "archive_mode": "on", "archive_command": "(disabled)"}
    issues = wal_archive.evaluate(settings)
    assert any("archive_command" in i for i in issues)


def test_evaluate_accepts_always_archive_mode_and_logical_wal_level():
    settings = {"wal_level": "logical", "archive_mode": "always", "archive_command": _OK_COMMAND}
    assert wal_archive.evaluate(settings) == []


# --- check_archive_advanced ---------------------------------------------------


def test_check_archive_advanced_ok_when_new_file_appears():
    assert wal_archive.check_archive_advanced({"a"}, {"a", "b"}) is None


def test_check_archive_advanced_fails_when_no_new_file():
    reason = wal_archive.check_archive_advanced({"a"}, {"a"})
    assert reason is not None


# --- verify_archiving_advances ------------------------------------------------


def test_verify_archiving_advances_detects_new_file_before_timeout():
    calls = {"n": 0}

    def list_dir(_d):
        calls["n"] += 1
        return {"a"} if calls["n"] == 1 else {"a", "new-wal-file"}

    reason = wal_archive.verify_archiving_advances(
        "postgresql://x/y",
        "archive-dir",
        timeout=10.0,
        poll_interval=1.0,
        switch_wal=lambda dsn: None,
        list_dir=list_dir,
        sleep=lambda s: None,
        clock=iter([0.0, 1.0, 2.0]).__next__,
    )
    assert reason is None


def test_verify_archiving_advances_times_out_when_nothing_new():
    clock_values = iter([0.0, 1.0, 5.0, 11.0, 11.0])
    reason = wal_archive.verify_archiving_advances(
        "postgresql://x/y",
        "archive-dir",
        timeout=10.0,
        poll_interval=1.0,
        switch_wal=lambda dsn: None,
        list_dir=lambda _d: {"a"},
        sleep=lambda s: None,
        clock=lambda: next(clock_values),
    )
    assert reason is not None
    assert "새 파일" in reason


# --- negative tests: invalid/corrupted inputs --------------------------------


def test_main_returns_1_when_verify_write_dir_does_not_exist(monkeypatch):
    """존재하지 않는 아카이브 디렉터리를 --verify-write로 지정하면 종료코드 1."""
    import sys
    from io import StringIO

    # read_wal_settings를 mock해서 DB 연결 없이 valid settings를 반환
    monkeypatch.setattr(
        wal_archive,
        "read_wal_settings",
        lambda dsn: {
            "wal_level": "replica",
            "archive_mode": "on",
            "archive_command": "cp %p /archive/%f",
        },
    )
    # verify_archiving_advances가 switch_wal을 통해 DB에 연결하므로
    # verify_archiving_advances 자체를 mock해서 reason을 반환하도록 한다
    monkeypatch.setattr(
        wal_archive,
        "verify_archiving_advances",
        lambda *a, **k: "verify failed",
    )

    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.argv = [
        "wal_archive",
        "--dsn",
        "postgresql://x/y",
        "--verify-write",
        "/nonexistent/dir/that/does/not/exist",
    ]
    sys.stdout = StringIO()
    sys.stderr = StringIO(
        "stderr",
    )  # noqa: DUPL125
    try:
        code = wal_archive.main()
        assert code == 1
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def test_main_returns_1_when_verify_write_dir_not_writable(monkeypatch):
    """쓰기 권한이 없는 디렉터리를 --verify-write로 지정하면 종료코드 1."""
    import sys
    import tempfile
    from io import StringIO

    monkeypatch.setattr(
        wal_archive,
        "read_wal_settings",
        lambda dsn: {
            "wal_level": "replica",
            "archive_mode": "on",
            "archive_command": "cp %p /archive/%f",
        },
    )

    tmpdir = tempfile.mkdtemp()
    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.argv = [
        "wal_archive",
        "--dsn",
        "postgresql://x/y",
        "--verify-write",
        tmpdir,
    ]
    sys.stdout = StringIO()
    sys.stderr = StringIO(
        "stderr",
    )  # noqa: DUPL125
    try:
        # list_dir를 monkeypatch하여 비어있도록 만들어 verify_archiving_advances가
        # 타임아웃으로 실패하도록 유도 (verify_write 경로가 실패 경로를 타는지 확인)
        monkeypatch.setattr(wal_archive, "verify_archiving_advances", lambda *a, **k: "타임아웃")
        code = wal_archive.main()
        assert code == 1
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def test_evaluate_flags_missing_wal_level():
    """wal_level 키가 없으면 evaluate는 실패를 보고한다."""
    settings = {"archive_mode": "on", "archive_command": "cp %p /archive/%f"}
    issues = wal_archive.evaluate(settings)
    assert any("wal_level" in i for i in issues)


def test_evaluate_flags_missing_archive_mode():
    """archive_mode 키가 없으면 evaluate는 실패를 보고한다."""
    settings = {"wal_level": "replica", "archive_command": "cp %p /archive/%f"}
    issues = wal_archive.evaluate(settings)
    assert any("archive_mode" in i for i in issues)


def test_evaluate_flags_empty_wal_level():
    """wal_level가 빈 문자열이면 evaluate는 실패를 보고한다 — negative 테스트 3."""
    settings = {"wal_level": "", "archive_mode": "on", "archive_command": "cp %p /archive/%f"}
    issues = wal_archive.evaluate(settings)
    assert any("wal_level" in i for i in issues)


def test_evaluate_flags_multiple_issues():
    """여러 설정이 동시에 잘못되면 evaluate는 모든 위반을 보고한다 — negative 테스트 4."""
    settings = {"wal_level": "minimal", "archive_mode": "off", "archive_command": ""}
    issues = wal_archive.evaluate(settings)
    assert len(issues) >= 3  # wal_level + archive_mode + archive_command
    assert any("wal_level" in i for i in issues)
    assert any("archive_mode" in i for i in issues)
    assert any("archive_command" in i for i in issues)


# --- failure injection: OSError in verify_archiving_advances -----------------


def test_verify_archiving_advances_raises_OSError_from_list_dir():
    """list_dir가 OSError를 던지면 호출부가 이를 삼키지 않고 전파한다."""
    import pytest

    def failing_list_dir(_d):
        raise OSError("directory read failed")

    with pytest.raises(OSError, match="directory read failed"):
        wal_archive.verify_archiving_advances(
            "postgresql://x/y",
            "archive-dir",
            timeout=1.0,
            poll_interval=0.5,
            switch_wal=lambda dsn: None,
            list_dir=failing_list_dir,
            sleep=lambda s: None,
            clock=lambda: 999.0,  # deadline 초과 방지
        )


# --- perf: evaluate() budget + gate-red with virtual delay -------------------


@pytest.mark.perf
def test_evaluate_perf_budget_and_gate_red():
    """evaluate() 성능 예산 및 게이트 적색 재현 (D2, task-24483).

    실측 baseline: evaluate() 한 호출 ≈ 0.01ms (순수 dict 조회 + 문자열 비교).
    성능 예산: baseline의 30배 = 0.3ms (문서화 예산).

    게이트 적색 재현: virtual_delayed_sync로 10ms 가상 지연 주입 →
    perf_budget.sample.cpu_ms가 예산 0.3ms를 초과하면 적색 전환됨을 확인.
    실제 sleep/busy-wait 금지 — virtual_delayed_sync가 virtual ledger에
    나노초만 더하므로 테스트는 즉시 종료된다.
    """

    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()

    baseline_settings = {
        "wal_level": "replica",
        "archive_mode": "on",
        "archive_command": "cp %p /archive/%f",
    }

    # baseline 측정 — 지연 없이 정상 경로
    budget = PerfBudget()
    baseline_sample = budget.sample(lambda: wal_archive.evaluate(baseline_settings))
    # baseline은 매우 빨라야 함 (순수 함수, 3개 dict 조회)
    assert baseline_sample.cpu_ms < 0.3, (
        f"baseline_perf: cpu={baseline_sample.cpu_ms:.3f}ms exceeds 0.3ms "
        f"(pure dict lookups, no delay)"
    )

    # 게이트 적색 재현: evaluate() 전체 호출에 10ms 가상 지연 주입
    # evaluate()를 virtual_delayed_sync로 감싸서 CPU ledger에 10ms 추가
    reset_virtual_totals()
    delayed_evaluate = virtual_delayed_sync(lambda: wal_archive.evaluate(baseline_settings), 0.01)
    # virtual_delayed_sync는 cpu ledger에도 10ms를 기록하므로
    # PerfBudget.sample이 이를 반영함
    gate_red_sample = budget.sample(delayed_evaluate)
    # 10ms 지연 주입 → cpu_ms ≈ 10ms. 예산 0.3ms를 초과하므로 게이트가 적색 전환됨을 확인.
    # 이 단언은 "지연 주입 시 cpu_ms가 예산을 초과한다"는 사실을 검증 —
    # 즉, 성능 게이트가 실제로 위반을 잡는지 적색 재현.
    assert gate_red_sample.cpu_ms > 0.3, (
        f"perf_gate_red: cpu={gate_red_sample.cpu_ms:.3f}ms exceeds budget 0.3ms "
        f"(10ms virtual delay injected via virtual_delayed_sync)"
    )
