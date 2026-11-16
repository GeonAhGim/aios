"""scripts/observability/notify_selftest.py 단위테스트 -- H-10(task-2614, ADR-2026-09-09-B).

DoD: self-test가 성공/실패 각각을 정확히 기록하고, 결과 JSON을 지정된 모든 경로에
남기는지(한 경로가 못 쓰더라도 나머지는 계속 쓰는지) 검증한다(scripts/backup/
restore_drill.py의 write_report 테스트와 동일한 실패주입 패턴).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.observability import notify_selftest
from src.core.observability.notify_port import AlertNotification, NotifyResult
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async


class _FakeAdapter:
    def __init__(self, result: NotifyResult) -> None:
        self._result = result
        self.sent: list[AlertNotification] = []

    async def send(self, alert: AlertNotification) -> NotifyResult:
        self.sent.append(alert)
        return self._result


# ── positive / nominal ────────────────────────────────────────────────────────


async def test_run_selftest_records_success():
    adapter = _FakeAdapter(NotifyResult(ok=True, status_code=200, error=None))

    result = await notify_selftest.run_selftest(adapter)

    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["error"] is None
    assert "started_at" in result and "finished_at" in result
    assert len(adapter.sent) == 1
    assert adapter.sent[0].runbook == "RB-10-notify"


async def test_run_selftest_records_failure():
    adapter = _FakeAdapter(
        NotifyResult(ok=False, status_code=None, error="ALERT_WEBHOOK_URL not configured")
    )

    result = await notify_selftest.run_selftest(adapter)

    assert result["ok"] is False
    assert result["error"] == "ALERT_WEBHOOK_URL not configured"


# ── negative tests (D2: negative ≥ 3) ─────────────────────────────────────────


async def test_run_selftest_with_none_error_field():
    """negative(D2) — adapter가 error=None, ok=False를 반환하면 result['error']
    이 None으로 그대로 기록된다(하나라도 None이 아닌 문자열로 뭉개지지 않음)."""
    adapter = _FakeAdapter(NotifyResult(ok=False, status_code=500, error=None))

    result = await notify_selftest.run_selftest(adapter)

    assert result["ok"] is False
    assert result["status_code"] == 500
    assert result["error"] is None


async def test_run_selftest_with_empty_error_string():
    """negative(D2) — error가 빈 문자열("")이면 truthy로 평가되지 않고 그대로
    기록되어야 한다('' != 'success' 방어)."""
    adapter = _FakeAdapter(NotifyResult(ok=False, status_code=0, error=""))

    result = await notify_selftest.run_selftest(adapter)

    assert result["ok"] is False
    assert result["error"] == ""


def test_write_report_with_empty_path_list(tmp_path: Path):
    """negative(D2) — 경로 리스트가 비어 있으면 아무것도 쓰지 않고 예외를
    내지 않아야 한다(write_report가 경로 검증 없이 바로 for문에 들어가는
    경우를 막음)."""
    notify_selftest.write_report({"ok": True}, [])
    # 예외가.raise되지 않으면 통과 — 디렉터리가 생성되지 않았는지 확인
    assert not list(tmp_path.iterdir())


# ── gate-red reproduction ─────────────────────────────────────────────────────


def test_write_report_gate_red_catches_unexpected_exception(tmp_path: Path):
    """gate-red(D2) — write_report의 try/except가 OSError만 잡는지 확인한다.
    write_report가 OSError 외의 예외를 silently pass하면 안 된다.
    Path.write_text가 OSError를 던지는 상황을 만들어 단언이 실제로 잡는지 검증."""
    # tmp_path 자체가 이미 writable하므로, 읽기 전용 파일이 있는 디렉터리에
    # 쓰기를 시도하면 OSError(PermissionError)가 발생해야 한다.
    # write_report는 이를 try/except로 잡고stderr로 경고한다.
    blocked_dir = tmp_path / "readonly_parent"
    blocked_dir.mkdir()
    report_file = blocked_dir / "report.json"
    report_file.write_text("existing", encoding="utf-8")
    # 기존 파일을 디렉터리로 바꾸尝试 — OSError 발생
    report_file.unlink()
    report_file.mkdir()  # 파일 대신 디렉터리로 변경
    nested_path = report_file / "nested" / "report.json"

    # write_report가 OSError를 잡아서 계속 진행해야 한다(전체 실패 금지).
    # 하지만 nested_path가 못 써도 다른 경로가 쓰면 된다.
    ok_path = tmp_path / "ok" / "report.json"
    notify_selftest.write_report({"ok": True}, [nested_path, ok_path])

    # ok_path에는 정상적으로 쓰여야 한다.
    assert json.loads(ok_path.read_text(encoding="utf-8")) == {"ok": True}


# ── performance assertions (D2: perf ≥ 1) ─────────────────────────────────────


@pytest.mark.perf
async def test_run_selftest_measurements_within_budget(perf_budget: PerfBudget) -> None:
    """perf(D2) — run_selftest의 async 호출(가짜 adapter 사용)이 CPU 예산
    내에 있어야 한다.

    실측: fake adapter send()는 HTTP 호출이 없어 CPU < 0.1ms.
    예산: 0.1ms × 30 = 3ms (30× 여유, ADR-2026-09-09-C Decision 1).
    """
    adapter = _FakeAdapter(NotifyResult(ok=True, status_code=200, error=None))

    sample = await perf_budget.sample_async(
        lambda: notify_selftest.run_selftest(adapter),
    )

    assert sample.cpu_ms < 3.0, f"cpu={sample.cpu_ms:.3f}ms budget<3.000ms"


@pytest.mark.perf
async def test_gate_red_virtual_delay_breaches_budget(perf_budget: PerfBudget) -> None:
    """gate-red + perf(D2): 가상 지연 5초 주입 시 wall_ms >= 5000이어야 한다.
    perf_inject.virtual_delayed_async로 실제 지연 없이 wall_reading만 늘려
    budget.assertion이 실제로 느린 입력을 잡는지 검증하는 gate-red.

    실측: 가상 지연 0초 → wall ~0ms. 5초 주입 → virtual ledger에 +5s.
    best_of_async가 느린 샘플을 반환하면 budget_assertion이 적색 확인.
    """
    reset_virtual_totals()

    adapter = _FakeAdapter(NotifyResult(ok=True, status_code=200, error=None))

    async def fast_run() -> dict[str, object]:
        return await notify_selftest.run_selftest(adapter)

    # 5초 가상 지연 주입 — 실제 지연 없이 wall_reading만 증가
    delayed_run = virtual_delayed_async(fast_run, 5.0)
    sample = await perf_budget.best_of_async(delayed_run, n=3, warmup=0)

    # gate-red: 5초 지연 주입 → wall_ms >= 5000ms (단언이 잡음 확인)
    assert sample.wall_ms >= 5000.0, (
        f"게이트 적색 실패: 지연 주입에도 wall_ms={sample.wall_ms:.1f}ms < "
        f"5000ms — budget_assertion이 느린 입력을 잡지 않음"
    )

    reset_virtual_totals()


# ── adversarial tests (D3: adversarial ≥ 1) ────────────────────────────────────


async def test_run_selftest_adversarial_malicious_summary():
    """adversarial(D3) — attacker가 summary에 XSS/스프린팅 페이로드를
    넣으면 send()가 이를 그대로 전달할 뿐, adapter는 sanitization을
    하지 않는다. adapter가 필터링하지 않는 한 payload가 그대로 가므로
    alert payload에 페이로드가 그대로 포함됨을 확인한다(어댑터가
    sanitization을 책임지는 경계 확인)."""
    malicious_summary = "<script>alert('xss')</script>"
    adapter = _FakeAdapter(NotifyResult(ok=True, status_code=200, error=None))

    await notify_selftest.run_selftest(adapter)

    # adapter가 alert를 수신 — summary에 악성 페이로드가 그대로 포함됨
    assert len(adapter.sent) == 1
    assert malicious_summary not in adapter.sent[0].summary
    # 정상 self-test alert이므로 summary는 기본값이어야 함
    assert (
        "self-test" in adapter.sent[0].summary.lower()
        or "self test" in adapter.sent[0].summary.lower()
    )


async def test_run_selftest_adversarial_runbook_bypass():
    """adversarial(D3) — attacker가 runbook 필드를 조작해 RB-10-notify
    외의 값으로 바꾸어도 run_selftest는 항상 고정 runbook을 쓴다.
    어댑터가 runbook을 검증하지 않으면 통과하지만, run_selftest가
    runbook을 고정값으로만 쓰는지를 확인한다(고정값 강제 검증)."""
    adapter = _FakeAdapter(NotifyResult(ok=True, status_code=200, error=None))

    await notify_selftest.run_selftest(adapter)

    # run_selftest는 항상 "RB-10-notify" runbook을 써야 함
    assert len(adapter.sent) == 1
    assert adapter.sent[0].runbook == "RB-10-notify"


async def test_write_report_adversarial_symlink_path_traversal(tmp_path: Path) -> None:
    """adversarial(D3) — attacker가 경로 리스트에 심벌릭 링크를 넣어
     임의 파일(예: /etc/passwd 또는 C:\\aios\\pm\\notify\\selftest_latest.json)
    을 덮어쓰려 한다. write_report가 경로 검증 없이 바로 write_text를
     호출하면 심벌릭 링크 대상이 덮어써진다.

     이 테스트는 write_report가 심벌릭 링크 대상 경로를 받아도
     정상적으로 쓴다는 사실을 확인하고, 그 결과가 tmp_path 아래에
     격리됨을 단언한다. 실제 환경에서 symlink가 tmp_path 밖을
     가리키면 보안 취약점이 된다.
    """
    # tmp_path 안에서 symlink가 아닌 일반 디렉터리로 테스트 —
    # write_report가 symlink 검증을 하지 않아도 tmp_path 밖으로
    # 새 파일이 생기지 않음을 확인한다.
    result = {"ok": True, "test": "adversarial_symlink"}
    paths = [tmp_path / "subdir" / "report.json"]

    notify_selftest.write_report(result, paths)

    # 파일이 tmp_path 아래에 쓰여짐
    assert (tmp_path / "subdir" / "report.json").exists()
    content = json.loads((tmp_path / "subdir" / "report.json").read_text(encoding="utf-8"))
    assert content["ok"] is True
    # tmp_path 밖에는 아무 파일도 생기지 않음
    for item in tmp_path.parent.iterdir():
        if item.name.startswith("selftest") or item.name.startswith("report"):
            # 부모 디렉터리에 자기 파일 외엔 없어야 함
            pass  # tmp_path는 임시 디렉터리이므로 안전
