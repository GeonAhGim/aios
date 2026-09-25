"""scripts/observability/notify_selftest.py 단위테스트 -- H-10(task-2614, ADR-2026-09-09-B).

DoD: self-test가 성공/실패 각각을 정확히 기록하고, 결과 JSON을 지정된 모든 경로에
남기는지(한 경로가 못 쓰더라도 나머지는 계속 쓰는지) 검증한다(scripts/backup/
restore_drill.py의 write_report 테스트와 동일한 실패주입 패턴).
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.observability import notify_selftest
from src.core.observability.notify_port import AlertNotification, NotifyResult


class _FakeAdapter:
    def __init__(self, result: NotifyResult) -> None:
        self._result = result
        self.sent: list[AlertNotification] = []

    async def send(self, alert: AlertNotification) -> NotifyResult:
        self.sent.append(alert)
        return self._result


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


def test_write_report_writes_json_to_every_path(tmp_path: Path):
    result = {"ok": True}
    paths = [tmp_path / "a" / "selftest_latest.json", tmp_path / "b" / "selftest_latest.json"]

    notify_selftest.write_report(result, paths)

    for p in paths:
        assert json.loads(p.read_text(encoding="utf-8")) == result


def test_write_report_does_not_raise_when_one_path_unwritable(tmp_path: Path):
    unwritable_parent = tmp_path / "blocked"
    unwritable_parent.write_text("i am a file, not a dir", encoding="utf-8")
    ok_path = tmp_path / "ok" / "selftest_latest.json"

    notify_selftest.write_report(
        {"ok": False}, [unwritable_parent / "selftest_latest.json", ok_path]
    )

    assert json.loads(ok_path.read_text(encoding="utf-8")) == {"ok": False}
