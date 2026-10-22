"""ops/backup_verify.py 단위 테스트 -- FA-20(task-2677, ADR-2026-09-09-C).

`run_backup_verify`는 새 PITR 로직을 추가하지 않는다(scripts/backup/{base_backup,
wal_archive,restore_drill}.py가 각 단계 자체는 이미 전용 테스트로 증명한다) -- 이
파일의 대상은 "세 단계를 어떤 순서로, 어떤 단락(short-circuit) 조건으로 묶는가"와
"스케줄 루프가 한 주기 실패에도 죽지 않는가"다. 각 단계는 주입 가능한 콜러블이라
실제 pg_basebackup/asyncpg/pg_ctl 없이 전체 시나리오를 재현한다.

ADR-2026-09-09-C D2(완료 하한, 안전축은 D3) 증빙:
1. 실패 주입 -- 각 단계 실패가 뒷 단계를 막는지(1개는 예외 raise 경로).
2. 성능 단언 -- 오케스트레이션 오버헤드(단계 자체 지연 제외) 예산.
3. 게이트 적색 재현 -- WAL 아카이빙 위반이 복구 리허설을 실행조차 못 하게 막고,
   그 사실이 steps에 정확히 남는지.
4. D3 안전축 -- 동시 다중 인스턴스(스레드)가 서로 오염되지 않고, 같은 입력이
   항상 같은 결과(리플레이 결정론)를 냄.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from ops import backup_verify
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import delayed_sync


def _ok_base_backup(dest_dir: Path, dsn: str) -> dict[str, Any]:
    return {"ok": True, "dest": str(dest_dir / "20260910T000000Z"), "tail": "backup ok"}


def _failing_base_backup(dest_dir: Path, dsn: str) -> dict[str, Any]:
    return {"ok": False, "dest": "", "tail": "pg_basebackup rc=1"}


def _raising_base_backup(dest_dir: Path, dsn: str) -> dict[str, Any]:
    raise RuntimeError("pg_basebackup 실행 파일을 찾을 수 없습니다")


def _clean_wal(dsn: str) -> dict[str, str]:
    return {"wal_level": "replica", "archive_mode": "on", "archive_command": "cp %p /archive/%f"}


def _dirty_wal(dsn: str) -> dict[str, str]:
    return {"wal_level": "minimal", "archive_mode": "off", "archive_command": ""}


def _ok_drill(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, "steps": {"replay_verify": {"ok": True}}}


def _failing_drill(**kwargs: Any) -> dict[str, Any]:
    return {"ok": False, "steps": {"replay_verify": {"ok": False, "detail": "mismatch"}}}


def _common_kwargs(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        backup_dest_dir=tmp_path / "base",
        archive_dir=tmp_path / "archive",
        restore_data_dir=tmp_path / "restore_pgdata",
        restore_port=15433,
        dsn="postgresql://user@dbhost:5432/aios_dev",
        repo_root=tmp_path,
        base_backup=_ok_base_backup,
        read_wal=_clean_wal,
        verify_write=lambda dsn, archive_dir: None,
        drill=_ok_drill,
    )
    kwargs.update(overrides)
    return kwargs


# --- 성공 경로 ------------------------------------------------------------------


def test_success_path_runs_all_three_stages_in_order(tmp_path: Path) -> None:
    calls: list[str] = []

    def base_backup(dest_dir: Path, dsn: str) -> dict[str, Any]:
        calls.append("base_backup")
        return _ok_base_backup(dest_dir, dsn)

    def read_wal(dsn: str) -> dict[str, str]:
        calls.append("wal_archive")
        return _clean_wal(dsn)

    def drill(**kwargs: Any) -> dict[str, Any]:
        calls.append("restore_drill")
        return _ok_drill(**kwargs)

    result = backup_verify.run_backup_verify(
        **_common_kwargs(tmp_path, base_backup=base_backup, read_wal=read_wal, drill=drill)
    )

    assert result["ok"] is True
    assert calls == ["base_backup", "wal_archive", "restore_drill"]
    assert set(result["steps"]) == {"base_backup", "wal_archive", "restore_drill"}


# --- 실패 주입(D2) -- 각 단계 실패가 뒷 단계를 막는다 --------------------------------


def test_base_backup_failure_halts_before_wal_archive_and_drill(tmp_path: Path) -> None:
    wal_called = False

    def read_wal(dsn: str) -> dict[str, str]:
        nonlocal wal_called
        wal_called = True
        return _clean_wal(dsn)

    result = backup_verify.run_backup_verify(
        **_common_kwargs(tmp_path, base_backup=_failing_base_backup, read_wal=read_wal)
    )

    assert result["ok"] is False
    assert result["steps"]["base_backup"]["ok"] is False
    assert "wal_archive" not in result["steps"]
    assert "restore_drill" not in result["steps"]
    assert wal_called is False


def test_base_backup_empty_tail_never_yields_empty_detail(tmp_path: Path) -> None:
    """task-9469: 2026-09-16부터 재발한 관측 사고 재현 -- pg_basebackup이 강제 종료돼
    stdout/stderr을 하나도 못 남기면 base_backup.py의 manifest["tail"]이 ""가 된다.
    이 케이스에서 detail이 빈 문자열로 새어나가면 esc-health-backup_drill_failed를
    사람이 진단할 근거가 하나도 남지 않는다 -- rc·타임아웃 여부·소요 시간을 대신 채워
    detail이 절대 비지 않게 한다."""

    def _empty_tail_timeout_base_backup(dest_dir: Path, dsn: str) -> dict[str, Any]:
        return {
            "ok": False,
            "dest": "",
            "returncode": 124,
            "tail": "",
            "started_at": "2026-09-30T01:04:03+00:00",
            "finished_at": "2026-09-30T01:31:05+00:00",
        }

    result = backup_verify.run_backup_verify(
        **_common_kwargs(tmp_path, base_backup=_empty_tail_timeout_base_backup)
    )

    detail = result["steps"]["base_backup"]["detail"]
    assert detail != ""
    assert "rc=124" in detail
    assert "timeout" in detail
    assert "1622s" in detail  # 27분 = 1622초, 소요 시간이 남아야 진단 가능하다


def test_base_backup_raising_runtime_error_is_caught_and_halts(tmp_path: Path) -> None:
    """pg_basebackup 바이너리가 PATH에 없으면 run_base_backup은 RuntimeError를 던진다
    (base_backup.py의 which() 체크) -- 이 예외가 오케스트레이터 밖으로 새면 스케줄
    루프가 매 주기 크래시하므로 여기서 잡아 steps로 흡수해야 한다."""
    result = backup_verify.run_backup_verify(
        **_common_kwargs(tmp_path, base_backup=_raising_base_backup)
    )

    assert result["ok"] is False
    assert result["steps"]["base_backup"]["ok"] is False
    assert "찾을 수 없습니다" in result["steps"]["base_backup"]["detail"]
    assert "wal_archive" not in result["steps"]


def test_wal_archive_issue_halts_before_drill(tmp_path: Path) -> None:
    drill_called = False

    def drill(**kwargs: Any) -> dict[str, Any]:
        nonlocal drill_called
        drill_called = True
        return _ok_drill(**kwargs)

    result = backup_verify.run_backup_verify(
        **_common_kwargs(tmp_path, read_wal=_dirty_wal, drill=drill)
    )

    assert result["ok"] is False
    assert result["steps"]["wal_archive"]["ok"] is False
    assert "restore_drill" not in result["steps"]
    assert drill_called is False


def test_restore_drill_failure_is_reflected_and_is_the_final_step(tmp_path: Path) -> None:
    result = backup_verify.run_backup_verify(**_common_kwargs(tmp_path, drill=_failing_drill))

    assert result["ok"] is False
    assert result["steps"]["restore_drill"]["ok"] is False
    assert set(result["steps"]) == {"base_backup", "wal_archive", "restore_drill"}


@pytest.mark.parametrize("garbage_interval", [0, -1, -0.5])
def test_run_scheduled_rejects_nonpositive_interval_fail_closed(garbage_interval: float) -> None:
    """0이나 음수 주기를 주면 바로 예외를 내야 한다 -- 그렇지 않으면 sleep(0) 또는
    sleep(음수)로 바쁜루프/즉시예외에 빠져 스케줄이 아닌 폭주가 된다."""
    with pytest.raises(ValueError):
        backup_verify.run_scheduled(
            interval_hours=garbage_interval,
            run_once=lambda: {"ok": True, "steps": {}},
            report_paths=[],
        )


# --- 게이트 적색 재현(D2) -- WAL 위반이 복구 리허설을 실행조차 못 하게 막는다 -----------


def test_gate_red_wal_violation_blocks_drill_and_reports_exact_stop_point(tmp_path: Path) -> None:
    """실제 운영 시나리오 재현: 베이스 백업은 성공했지만 그 사이 archive_command가
    죽어 WAL 아카이빙이 끊긴 상태(wal_archive.evaluate가 위반을 낸다)에서 정기
    리허설이 돈다. 게이트가 적색이 되어 restore_drill은 단 한 번도 호출되지 않아야
    하고(호출됐다면 존재하지 않는 WAL을 찾으려다 다른 방식으로 실패해 원인이 흐려진다),
    steps에는 정확히 base_backup=ok, wal_archive=violation까지만 남는다."""
    drill_invocations: list[dict[str, Any]] = []

    def spy_drill(**kwargs: Any) -> dict[str, Any]:
        drill_invocations.append(kwargs)
        return _ok_drill(**kwargs)

    result = backup_verify.run_backup_verify(
        **_common_kwargs(tmp_path, read_wal=_dirty_wal, drill=spy_drill)
    )

    assert result["ok"] is False
    assert result["steps"]["base_backup"]["ok"] is True
    assert result["steps"]["wal_archive"]["ok"] is False
    assert "wal_level" in result["steps"]["wal_archive"]["detail"]
    assert "archive_command" in result["steps"]["wal_archive"]["detail"]
    assert drill_invocations == []
    assert "restore_drill" not in result["steps"]


def test_run_scheduled_survives_run_once_exception_and_retries_next_cycle() -> None:
    """실패 주입: run_once 자체가 예외를 던지는 주기가 있어도(예: DSN 파싱 버그,
    네트워크 일시 단절) 루프는 죽지 않고 다음 주기를 이어간다 -- 하루 1회
    스케줄에서 한 번의 예외로 이후 모든 리허설이 영구히 멈추면 안 된다."""
    calls = 0

    def flaky_run_once() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("일시적 DB 접속 실패")
        return {"ok": True, "steps": {}}

    backup_verify.run_scheduled(
        interval_hours=1.0,
        run_once=flaky_run_once,
        report_paths=[],
        sleep=lambda s: None,
        max_iterations=3,
    )

    assert calls == 3  # 1번째 예외 이후에도 2, 3번째가 실행됐다


def test_run_scheduled_sleeps_interval_hours_in_seconds_between_cycles() -> None:
    sleeps: list[float] = []
    backup_verify.run_scheduled(
        interval_hours=2.0,
        run_once=lambda: {"ok": True, "steps": {}},
        report_paths=[],
        sleep=sleeps.append,
        max_iterations=3,
    )
    assert sleeps == [7200.0, 7200.0]  # 마지막 주기 뒤에는 sleep하지 않는다


# --- 성능 단언(D2) ----------------------------------------------------------------


@pytest.mark.perf
def test_run_backup_verify_orchestration_overhead_within_budget(
    tmp_path: Path, perf_budget: PerfBudget
) -> None:
    """raw time.perf_counter() → perf_budget.sample(batch=1) 전환 (task-11918).

    process_time 기반 측정 + coverage tracer-pause로 부하 민감 호스트에서의
    오탐을 줄인다. batch=1 for outer sample but 2_000 iterations inside fn
    so total CPU time spans multiple 15.6ms ticks → stable per-call average.
    Budget: 2.0s → 2000ms (single *1000 conversion, no μs/ms mix).
    """

    iterations = 2_000
    kwargs = _common_kwargs(tmp_path)

    def _batch() -> None:
        for _ in range(iterations):
            result = backup_verify.run_backup_verify(**kwargs)
            assert result["ok"] is True

    sample = perf_budget.sample(_batch, batch=1)
    print(f"[FA-20 backup_verify] {iterations} runs in {sample.cpu_ms:.3f}ms (budget<2000.0ms)")
    assert sample.cpu_ms < 2000.0, (
        f"오케스트레이션 {iterations}회가 예산(2000.0ms)을 넘었습니다({sample.cpu_ms:.3f}ms)"
    )


@pytest.mark.perf
def test_run_backup_verify_orchestration_overhead_fails_with_10x_delay(
    tmp_path: Path, perf_budget: PerfBudget
) -> None:
    """Failure injection -- CPU-bound 지연 주입 시 적색 확인.

    delayed_sync(0.002)를 각 호출에 추가하면 2000회 × 2ms CPU 부하 = 4000ms CPU
    가 되어 2000ms CPU 예산을 초과한다. 이 지연을 제거하면 다시 녹색이 된다.
    time.sleep 대신 delayed_sync를 쓰는 이유: time.sleep은 CPU 시간을 늘리지
    않으므로 CPU 예산 단언을 검증할 수 없다. delayed_sync는 busy-wait로 실제
    CPU 시간을 소모하므로 process_time 기반 측정이 예산 초과를 감지한다.
    """

    iterations = 2_000
    kwargs = _common_kwargs(tmp_path)

    def _batch_delayed() -> None:
        for _ in range(iterations):
            result = backup_verify.run_backup_verify(**kwargs)
            assert result["ok"] is True
            delayed_sync(lambda: None, 0.002)()  # 2ms CPU 부하 주입 → 2000회 × 2ms = 4000ms CPU

    sample = perf_budget.sample(_batch_delayed, batch=1)
    # delayed_sync는 CPU 시간을 늘리므로 cpu_ms 로 단언.
    # 4000ms CPU > 2000ms budget → 적색 검출력 확인
    assert sample.cpu_ms > 2000.0, f"delay injection failed to exceed budget: {sample.cpu_ms:.1f}ms"


# --- D3 안전축 -- 동시 다중 인스턴스 + 리플레이 결정론 --------------------------------


def test_run_backup_verify_is_deterministic_for_identical_fakes(tmp_path: Path) -> None:
    kwargs = _common_kwargs(tmp_path)
    first = backup_verify.run_backup_verify(**kwargs)
    second = backup_verify.run_backup_verify(**kwargs)
    assert first["ok"] == second["ok"] == True  # noqa: E712
    assert first["steps"].keys() == second["steps"].keys()


def test_concurrent_run_backup_verify_calls_do_not_cross_contaminate(tmp_path: Path) -> None:
    """서로 다른 결과(성공/wal 위반/drill 실패)를 내는 여러 워커가 동시에
    run_backup_verify를 호출해도 서로의 결과가 섞이지 않는다 -- 모듈 레벨 가변
    상태가 없다는 동시성 증거(안전축 D3)."""

    def _run_ok(i: int) -> tuple[int, dict[str, Any]]:
        return i, backup_verify.run_backup_verify(**_common_kwargs(tmp_path / f"ok-{i}"))

    def _run_wal_violation(i: int) -> tuple[int, dict[str, Any]]:
        return i, backup_verify.run_backup_verify(
            **_common_kwargs(tmp_path / f"wal-{i}", read_wal=_dirty_wal)
        )

    def _run_drill_failure(i: int) -> tuple[int, dict[str, Any]]:
        return i, backup_verify.run_backup_verify(
            **_common_kwargs(tmp_path / f"drill-{i}", drill=_failing_drill)
        )

    jobs = []
    for i in range(20):
        jobs.append((_run_ok, i))
        jobs.append((_run_wal_violation, i))
        jobs.append((_run_drill_failure, i))

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(fn, i) for fn, i in jobs]
        results = [f.result() for f in futures]

    ok_results = [r for (fn, _), (_, r) in zip(jobs, results, strict=True) if fn is _run_ok]
    wal_results = [
        r for (fn, _), (_, r) in zip(jobs, results, strict=True) if fn is _run_wal_violation
    ]
    drill_results = [
        r for (fn, _), (_, r) in zip(jobs, results, strict=True) if fn is _run_drill_failure
    ]

    assert len(ok_results) == len(wal_results) == len(drill_results) == 20
    assert all(r["ok"] is True for r in ok_results)
    assert all(r["steps"]["wal_archive"]["ok"] is False for r in wal_results)
    assert all("restore_drill" not in r["steps"] for r in wal_results)
    assert all(r["steps"]["restore_drill"]["ok"] is False for r in drill_results)


# --- CLI 배선 -----------------------------------------------------------------


def test_main_single_run_writes_report_and_returns_nonzero_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_path = tmp_path / "drill_latest.json"

    monkeypatch.setattr(
        backup_verify,
        "run_backup_verify",
        lambda **kwargs: {"ok": False, "steps": {"base_backup": {"ok": False, "detail": "x"}}},
    )
    monkeypatch.setattr(backup_verify, "server_url", lambda: "postgresql://x/y")

    rc = backup_verify.main(
        [
            "--archive-dir",
            str(tmp_path / "archive"),
            "--dest-dir",
            str(tmp_path / "base"),
            "--restore-data-dir",
            str(tmp_path / "restore"),
            "--report-path",
            str(report_path),
        ]
    )

    assert rc == 1
    assert report_path.exists()


def test_main_schedule_flag_invokes_run_scheduled_not_single_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled_calls: list[dict[str, Any]] = []

    def fake_run_scheduled(**kwargs: Any) -> None:
        scheduled_calls.append(kwargs)

    monkeypatch.setattr(backup_verify, "run_scheduled", fake_run_scheduled)
    monkeypatch.setattr(backup_verify, "server_url", lambda: "postgresql://x/y")

    rc = backup_verify.main(
        [
            "--archive-dir",
            str(tmp_path / "archive"),
            "--schedule-interval-hours",
            "6",
        ]
    )

    assert rc == 0
    assert len(scheduled_calls) == 1
    assert scheduled_calls[0]["interval_hours"] == 6.0
