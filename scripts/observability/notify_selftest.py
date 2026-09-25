"""H-10(task-2614, ADR-2026-09-09-B) -- 알림 경로 self-test: 무음 알림을 감지한다.

config/observability/alert_rules.yaml 11개 규칙이 사람에게 닿는지는 실제로 알림을
보내봐야 안다 -- 웹훅 URL이 맞는지, 자격증명이 살아있는지는 정적 설정만으로는
증명되지 않는다. `src/core/observability/notify_port.py`의 `WebhookNotifyAdapter`로
합성 self-test 알림 1건을 보내고, 결과를 JSON으로 남긴다(scripts/backup/
restore_drill.py와 동일한 배선): `runtime/notify/selftest_latest.json`(로컬) +
`C:\\aios\\pm\\notify\\selftest_latest.json`(fleet, healthcheck.check_notify_selftest()가
읽는다).

실행은 저장소 루트에서 모듈 형태로만: `python -m scripts.observability.notify_selftest`
(직접 실행하면 scripts/가 sys.path[0]이 되어 `from scripts...` import가 깨진다 --
scripts/backup/restore_drill.py와 동일 규약).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from src.core.observability.notify_port import (
    AlertNotification,
    NotifyPort,
    WebhookNotifyAdapter,
)

ROOT = Path(__file__).resolve().parents[2]
PM_REPORT_PATH = Path(r"C:\aios\pm") / "notify" / "selftest_latest.json"
LOCAL_REPORT_PATH = ROOT / "runtime" / "notify" / "selftest_latest.json"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def run_selftest(adapter: NotifyPort) -> dict[str, object]:
    started = _now()
    alert = AlertNotification(
        alertname="H10_NotifySelfTest",
        severity="warn",
        status="firing",
        summary="알림 경로 self-test -- 실제 장애 아님, 무시해도 됨",
        runbook="RB-10-notify",
        labels={"source": "notify_selftest"},
    )
    result = await adapter.send(alert)
    finished = _now()
    return {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "ok": result.ok,
        "status_code": result.status_code,
        "error": result.error,
    }


def write_report(result: dict[str, object], paths: list[Path]) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    for p in paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(payload, encoding="utf-8")
        except OSError as e:
            print(f"경고: {p}에 결과를 쓰지 못했다: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--webhook-url", help="생략하면 ALERT_WEBHOOK_URL(.env)을 쓴다")
    parser.add_argument(
        "--report-path",
        action="append",
        help="결과 JSON을 남길 경로(반복 가능). 생략 시 기본 2곳(로컬 + fleet)",
    )
    args = parser.parse_args(argv)

    adapter = WebhookNotifyAdapter(args.webhook_url)
    result = asyncio.run(run_selftest(adapter))

    report_paths = (
        [Path(p) for p in args.report_path]
        if args.report_path
        else [LOCAL_REPORT_PATH, PM_REPORT_PATH]
    )
    write_report(result, report_paths)

    out = sys.stdout if result["ok"] else sys.stderr
    print(json.dumps(result, ensure_ascii=False, indent=2), file=out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
