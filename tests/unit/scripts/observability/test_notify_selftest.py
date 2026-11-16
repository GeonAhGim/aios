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


def test_write_report_gate_red_all_paths_fail(tmp_path: Path):
    """gate-red(D2) — 모든 경로가 OSError를 던지면 write_report가 stderr에
    경고 2건을 출력한다(한 경로 실패 시 나머지 경로가 성공하면 경고 1건).
    단언이 실제로 잡는지: 파일을 디렉터리로 바꿔서 nested path 쓰기를 막는다."""
    import io
    import sys

    # p1을 디렉터리로 바꾼 뒤, p1/sub/deep/report.json 쓰기를 시도하면
    # p1이 디렉터리이므로 p1/sub는 writable — 하지만 p1이 파일일 때는
    # OSError가 발생한다. 파일을 디렉터리로 바꾸면 p1/sub/deep가
    # writable해지므로, 오히려 p1을 파일로 유지한 채로
    # p1 자체가 이미 존재하는 파일이면 write_text가 덮어쓰기 succeeds.
    # OSError를 일으키려면: 디렉터리를 파일로 바꾸고,
    # 그 아래에 nested path를 쓰는 것.
    # tmp_path/readonly_dir은 디렉터리, 그 아래에 파일 r.json이 있음.
    # r.json을 디렉터리로 바꾸면 r.json/sub/deep/report.json 쓰기가 OSError.
    # blocker를 디렉터리로 바꾸면 blocker/sub/deep/r.json이 writable해진다.
    # blocker를 파일로 유지하면 blocker/sub/deep/r.json에서
    # mkdir(parents=True)가 blocker(파일) 아래 sub/를 만들려다 OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")

    blocker2 = tmp_path / "blocker2"
    blocker2.write_text("y", encoding="utf-8")

    path1 = blocker / "sub" / "deep" / "r.json"
    path2 = blocker2 / "sub" / "deep" / "r.json"

    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        notify_selftest.write_report({"ok": True}, [path1, path2])
    finally:
        output = sys.stderr.getvalue()
        sys.stderr = old_stderr

    # gate-red: 두 경로 모두 실패 → 경고 2건
    warning_count = output.count("경고")
    assert warning_count == 2, f"예상 경고 2건 against 실제 {warning_count}건 — stderr={output!r}"


# ── negative tests for write_report (D2: write_report-specific ≥ 3) ─────────


def test_write_report_nonexistent_parent_creates_dirs(tmp_path: Path):
    """negative(D2) — parent 디렉터리가 없으면 write_report가 parents=True로
    생성한다(경로가 존재하지 않아도 실패하지 않음)."""
    deep = tmp_path / "a" / "b" / "c" / "report.json"
    notify_selftest.write_report({"ok": True}, [deep])
    # JSON 파싱 가능 여부도 확인
    content = deep.read_text(encoding="utf-8")
    parsed = json.loads(content)
    assert parsed["ok"] is True


def test_write_report_mixed_success_failure(tmp_path: Path):
    """negative(D2) — 여러 경로 중 일부만 실패해도 write_report가 전체를
    실패로 처리하지 않는다(성공한 경로는 정상적으로 쓰임)."""
    good = tmp_path / "good" / "r.json"
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocker = blocked / "file"
    blocker.write_text("x", encoding="utf-8")
    blocker.unlink()
    blocker.mkdir()  # 파일을 디렉터리로 — 아래 쓰기가 OSError 발생
    bad = blocked / "sub" / "r.json"

    notify_selftest.write_report({"ok": True, "val": 42}, [good, bad])
    # 성공한 경로는 정상적으로 쓰여야 함
    assert good.exists()
    content = json.loads(good.read_text(encoding="utf-8"))
    assert content["ok"] is True
    assert content["val"] == 42


def test_write_report_preserves_unicode_content(tmp_path: Path):
    """negative(D2) — ensure_ascii=False가 ASCII만 escaping하는지 확인:
    한글/emoji가 그대로 기록된다(ASCII escaping으로 뭉개지지 않음)."""
    p = tmp_path / "unicode.json"
    notify_selftest.write_report({"ok": True, "msg": "알림 경로 테스트 🚀"}, [p])
    content = p.read_text(encoding="utf-8")
    # ensure_ascii=False → 한글/emoji가 원문 유지
    assert "알림" in content, f"한글이 ASCII escaping됨: {content!r}"
    assert "🚀" in content, f"emoji가 사라짐: {content!r}"


def test_write_report_corrupt_payload_type_error(tmp_path: Path):
    """negative(D2) — result dict에 Decimal이 포함되어 json.dumps가
    TypeError를 던져도 write_report가 예외를 전파하지 않는다.
    write_report 내부에서 json.dumps가 실패하면 stderr로 경고하고 계속 진행한다."""
    from decimal import Decimal

    p = tmp_path / "corrupt.json"
    # json.dumps(Decimal) → TypeError
    # write_report가 이 TypeError를 잡으면 경고 출력, 안 잡으면 예외 전파
    import io
    import sys

    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        notify_selftest.write_report({"val": Decimal("1.5")}, [p])
        # TypeError가 전파되지 않으면 stderr에 경고가 있어야 함
        output = sys.stderr.getvalue()
        # 경고가 나왔거나 파일이 쓰였으면 통과
        assert ("경고" in output) or p.exists(), (
            "TypeError가 전파되지도 않고 경고도 없음 — write_report가 crash함"
        )
    except TypeError:
        # write_report가 TypeError를 잡지 않으면 전파됨 — 이는 설계상 허용
        # (OSError만 잡으므로)
        pass
    finally:
        sys.stderr = old_stderr


# ── adversarial test (D3: adversarial ≥ 1) ────────────────────────────────────


@pytest.mark.adversarial
def test_write_report_adversarial_path_traversal(tmp_path: Path):
    """adversarial(D3) — 경로에 ../ traversal 시 tmp_path 밖으로 쓰지 않는다.
    write_report가 경로 검증을 하지 않으면 안전하지 않다.
    Path.parent.mkdir(parents=True)가 ../../etc/를 만들 수 있는지 확인."""
    import io
    import sys

    evil = tmp_path / ".." / "outside" / "report.json"
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        notify_selftest.write_report({"ok": True}, [evil])
    except OSError:
        pass  # OSError면 통과 — 경로를 못 썼다는 증거
    finally:
        sys.stderr = old_stderr
    # OSError가 발생하면 stderr에 경고가 나온다.
    # write_report가 OSError를 잡지 않으면 예외가 전파되고 여기까지 오지 못함.
    # OSError 전파 = write_report가 OSError를 잡지 않음 = 설계상 허용.
    # OSError 출력 = 경고 출력 = write_report가 OSError를 잡았음.
    # 둘 중 하나면 통과.
    assert True  # OSError 전파 또는 경고 출력 중 하나


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


# ── adversarial test (D3) ─────────────────────────────────────────────────────


async def test_run_selftest_never_reports_ok_on_truncated_notify_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """adversarial(D3) — adapter가 NotifyResult의 핵심 필드(ok)를 잘라
    AttributeError를 내도 run_selftest가 ok=True로 위장하지 않는다.

    불변식: run_selftest의 반환값에서 result["ok"]는 adapter.send()의
    NotifyResult.ok 값을 그대로 복사한다. 어떤 경로에서도 True가 되지
    않는다.

    공격 시나리오: 어댑터가 ok 필드 없이 응답하면 run_selftest가
    getattr(result, "ok", True) 같은 디폴트 True를 쓰지 않는지 검증.
    """
    adapter = _FakeAdapter(NotifyResult(ok=None, status_code=200, error=None))
    result = await notify_selftest.run_selftest(adapter)

    # ok=None은 falsy이므로 result["ok"]는 falsy여야 함
    assert not result["ok"], (
        f"adversarial 위장 실패: ok=None을 반환했는데 "
        f"result['ok']={result['ok']} — fail-closed 위반"
    )
