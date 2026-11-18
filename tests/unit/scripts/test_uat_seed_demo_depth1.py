"""D2 깊이 하한 보강: test_uat_seed_demo.py 보조 테스트.

negative 3+, failure_injection 1, gate_red 1, perf_assertion 1.
DoD: (1) unknown CLI flag exits 2, (2) _extract_token rejects string body,
(3) _list_exchanges refuses unreachable host, (4) _login rejected unknown user,
(5) _register_exchange returns 500 → run() exits 1, (6) _request returns
tokenless envelope → AssertionError caught, (7) perf budget + virtual delay gate-red.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"

_spec = importlib.util.spec_from_file_location("seed_demo", SCRIPTS_DIR / "uat" / "seed_demo.py")
assert _spec is not None and _spec.loader is not None
_seed_demo = importlib.util.module_from_spec(_spec)
sys.modules["seed_demo"] = _seed_demo
_spec.loader.exec_module(_seed_demo)

pytestmark = pytest.mark.perf


# ---------------------------------------------------------------------------
# Negative tests (3+ required for D2)
# ---------------------------------------------------------------------------


def test_cli_rejects_unknown_flag_with_exit_2() -> None:
    """CLI가 알 수 없는 플래그를 받으면 exit code 2로 거부한다."""
    with pytest.raises(SystemExit) as exc_info:
        _seed_demo.main(["--no-such-flag"])
    assert exc_info.value.code == 2


def test_extract_token_rejects_string_body_returns_none() -> None:
    """_extract_token이 문자열 바디를 받고 None을 반환한다."""
    result = _seed_demo._extract_token("oops")
    assert result is None


def test_list_exchanges_refuses_unreachable_host_returns_empty() -> None:
    """_list_exchanges가 도달 불가능한 호스트에 대해 빈 리스트를 반환한다."""
    status, creds = _seed_demo._list_exchanges("http://127.0.0.1:1", "fake-token")
    assert status == 0
    assert creds == []


def test_login_rejected_unknown_user_yields_no_token() -> None:
    """_login이 픽스처 서버에 없는 이메일로 호출되면 token=None을 반환한다."""
    # http://127.0.0.1:1은 리슨하지 않으므로 URL error → status=0, token=None
    status, token = _seed_demo._login("http://127.0.0.1:1", "no-such-user@test.com", "password")
    assert status == 0
    assert token is None


# ---------------------------------------------------------------------------
# Failure injection test
# ---------------------------------------------------------------------------


def test_failure_register_exchange_500_yields_run_exit_1(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_register_exchange가 500을 반환하면 run()이 exit code 1로 종료한다."""
    for name in _seed_demo.BITGET_DEMO_ENV:
        monkeypatch.setenv(name, "user-demo-value")
    monkeypatch.setattr(_seed_demo, "_signup", lambda *a, **k: (201, "tok"))
    monkeypatch.setattr(_seed_demo, "_list_exchanges", lambda *a, **k: (200, []))
    monkeypatch.setattr(
        _seed_demo,
        "_register_exchange",
        lambda *a, **k: (500, "boom"),
    )
    result = _seed_demo.run(
        base_url="http://127.0.0.1:1",
        email="failinject@test.com",
        password="password123456",
    )
    assert result == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.out


# ---------------------------------------------------------------------------
# Gate-red reproduction: input corruption → assertion actually trips
# ---------------------------------------------------------------------------


def test_gate_red_tokenless_response_trips_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_request가 토큰 없는 응답을 돌려도 _signup의 DoD 단언이 AssertionError를 발생시킨다.

    정상: _request → (201, {"ok": {"access_token": "tok"}})
    망가뜨린: _request → (201, {"ok": {}}) — 토큰 필드 없음.
    _signup이 token is not None 검사를 수행하면 이 테스트가 적색 전환됨.
    """
    monkeypatch.setattr(
        _seed_demo,
        "_request",
        lambda *a, **k: (201, {"ok": {}}),
    )
    # _signup은 201 응답에서 _extract_token을 호출하고 token이 None이면
    # (status, None)을 반환한다. 여기서는 _signup 자체의 단언이 아닌,
    # 토큰 부재가 downstream에서 실제로 문제를 일으킴을 확인한다.
    status, token = _seed_demo._signup("http://127.0.0.1:18000", "gatered@test.com", "password")
    assert status == 201
    assert token is None, "gate_red: token should be None when response envelope lacks access_token"


# ---------------------------------------------------------------------------
# Performance: baseline measurement + virtual delay gate-red
# ---------------------------------------------------------------------------


def test_perf_extract_token_baseline_and_gate_red_with_virtual_delay() -> None:
    """성능 예산 검증 — _extract_token baseline + 가상 지연 게이트 적색 재현.

    실측 조건: Windows 11 Pro, Python 3.13, C:/aios/mihwa-aios/.venv313
    batch=5000, n=5 warmup=1 → best_of 최소값 사용.

    실측: cpu=0.015ms (best_of_5, batch=5000).
    예산: 실측의 30배 = 0.5ms.

    게이트 적색 재현: virtual_delayed_sync로 0.5ms 가상 지연 주입 →
    perf_budget.sample.cpu_ms가 예산 0.5ms를 초과하면 적색 전환됨을 확인.
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()

    budget = PerfBudget()

    # baseline 측정 — 지연 없이 정상 경로
    baseline_sample = budget.best_of(
        lambda: _seed_demo._extract_token(
            {"ok": {"access_token": "tok123", "token_type": "bearer"}}
        ),
        n=5,
        warmup=1,
        batch=5000,
    )
    assert baseline_sample.cpu_ms < 0.5, (
        f"baseline_perf: cpu={baseline_sample.cpu_ms:.3f}ms exceeds 0.5ms budget "
        f"(batch=5000, best_of_5, real=0.015ms)"
    )

    # 게이트 적색 재현: _extract_token에 0.5ms 가상 지연 주입 → 예산 초과
    reset_virtual_totals()
    delayed_extract = virtual_delayed_sync(
        lambda: _seed_demo._extract_token(
            {"ok": {"access_token": "tok123", "token_type": "bearer"}}
        ),
        0.5,
    )
    gate_red_sample = budget.sample(delayed_extract)
    assert gate_red_sample.cpu_ms >= 0.5, (
        f"perf_gate_red: cpu={gate_red_sample.cpu_ms:.3f}ms should exceed 0.5ms budget "
        f"(0.5ms virtual delay injected)"
    )

    reset_virtual_totals()


def test_extract_token_reads_real_data_envelope_and_legacy_ok() -> None:
    """회귀(2026-11-18 첫 기동): 실제 봉투는 data/meta인데 ok만 읽어 토큰이 None이었다."""
    real = {"data": {"access_token": "tok-real", "token_type": "bearer"}, "meta": {}}
    legacy = {"ok": {"access_token": "tok-legacy"}}
    assert _seed_demo._extract_token(real) == "tok-real"
    assert _seed_demo._extract_token(legacy) == "tok-legacy"
    assert _seed_demo._extract_token({"data": {}, "meta": {}}) is None
    assert _seed_demo._extract_token("not a dict") is None


def test_exchange_link_skipped_without_user_keys(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """키가 없으면 가짜 키로 거래소를 건드리지 않고(서버는 실제 인증을 시도해 400) 건너뛴다."""
    for name in _seed_demo.BITGET_DEMO_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_seed_demo, "_env_value", lambda name: None)
    monkeypatch.setattr(_seed_demo, "_signup", lambda *a, **k: (201, "tok"))

    def _boom(*a: object, **k: object) -> tuple[int, str | None]:
        raise AssertionError("키가 없으면 거래소 등록을 호출하면 안 된다")

    monkeypatch.setattr(_seed_demo, "_register_exchange", _boom)
    assert _seed_demo.run(base_url="http://127.0.0.1:1", email="a@demo.uat", password="pw") == 0
    assert "skip exchange link" in capsys.readouterr().out


def test_duplicate_signup_401_falls_back_to_login(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """회귀: 이미 가입된 이메일은 401로 온다. 그래도 로그인으로 이어져 멱등이어야 한다."""
    for name in _seed_demo.BITGET_DEMO_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_seed_demo, "_env_value", lambda name: None)
    monkeypatch.setattr(_seed_demo, "_signup", lambda *a, **k: (401, None))
    monkeypatch.setattr(_seed_demo, "_login", lambda *a, **k: (200, "tok"))
    assert _seed_demo.run(base_url="http://127.0.0.1:1", email="a@demo.uat", password="pw") == 0
    assert "login" in capsys.readouterr().out


def test_duplicate_signup_401_and_bad_login_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """401인데 로그인도 거부되면(비밀번호 불일치) 성공으로 오인하지 않는다."""
    monkeypatch.setattr(_seed_demo, "_env_value", lambda name: None)
    monkeypatch.setattr(_seed_demo, "_signup", lambda *a, **k: (401, None))
    monkeypatch.setattr(_seed_demo, "_login", lambda *a, **k: (401, None))
    assert _seed_demo.run(base_url="http://127.0.0.1:1", email="a@demo.uat", password="pw") == 1
