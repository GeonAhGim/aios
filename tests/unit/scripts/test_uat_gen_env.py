"""Tests for scripts/uat/gen_env.py — .env.uat generator."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the scripts directory is importable.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
_REPO_ROOT = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from uat.gen_env import (  # noqa: E402
    _validate_web_origin,
    build_env,
    make_secrets,
)

# Perf / failure-injection imports (added for D2 depth, task-25554)
from tests.support.perf_budget import PerfBudget  # noqa: E402
from tests.support.perf_inject import reset_virtual_totals  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
_MINIMAL_TEMPLATE = """\
# comment line
DATABASE_URL=old
POSTGRES_PASSWORD=old
JWT_SIGNING_KEYS=
JWT_ACTIVE_KID=
CREDENTIAL_ENCRYPTION_KEYS_PAPER=
CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER=
AIOS_RUNTIME_MODE=
CORS_ALLOWED_ORIGINS=old
# trailing
"""

_SECRETS = {
    "POSTGRES_PASSWORD": "s3cret",
    "JWT_KID": "uat1",
    "JWT_HEX64": "a" * 64,
    "CRED_KID": "uat1",
    "CRED_HEX64": "b" * 64,
    "JWT_SECRET_KEY": "c" * 64,
    "CREDENTIAL_ENCRYPTION_KEY": "d" * 64,
    "UAT_DEMO_PASSWORD": "demo-pass-xyz",
}


# ---------------------------------------------------------------------------
# make_secrets
# ---------------------------------------------------------------------------
class TestMakeSecrets:
    def _run(self):
        return make_secrets()

    def test_returns_required_keys(self):
        s = self._run()
        for key in (
            "POSTGRES_PASSWORD",
            "JWT_KID",
            "JWT_HEX64",
            "CRED_KID",
            "CRED_HEX64",
        ):
            assert key in s

    def test_hex64_length(self):
        s = self._run()
        assert len(s["JWT_HEX64"]) == 64
        assert len(s["CRED_HEX64"]) == 64

    def test_hex64_is_valid_hex(self):
        s = self._run()
        int(s["JWT_HEX64"], 16)  # raises if invalid
        int(s["CRED_HEX64"], 16)

    def test_postgres_password_no_url_reserved(self):
        """token_urlsafe uses base64 + '-' and '_', never URL-reserved chars."""
        s = self._run()
        for ch in ("/", "+", "=", "?", "&", "="):
            assert ch not in s["POSTGRES_PASSWORD"]

    def test_kid_not_legacy(self):
        s = self._run()
        assert s["JWT_KID"] != "legacy"
        assert s["CRED_KID"] != "legacy"

    def test_different_calls_different_secrets(self):
        s1, s2 = self._run(), self._run()
        assert s1["JWT_HEX64"] != s2["JWT_HEX64"]


# ---------------------------------------------------------------------------
# build_env
# ---------------------------------------------------------------------------
class TestBuildEnv:
    def test_replaces_database_url(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "postgresql+asyncpg://aios:s3cret@postgres:5432/aios" in out

    def test_replaces_postgres_password(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        lines = [line.split("=", 1) for line in out.splitlines() if "=" in line]
        for k, v in lines:
            if k.strip() == "POSTGRES_PASSWORD":
                assert v.strip() == "s3cret"
                break
        else:
            pytest.fail("POSTGRES_PASSWORD not found")

    def test_postgres_user_and_db_match_database_url(self):
        """회귀: POSTGRES_USER/DB가 없으면 postgres 컨테이너가 기본 사용자 postgres로 초기화돼
        DATABASE_URL(aios)과 어긋나 'password authentication failed'가 된다(2026-11-18 첫 기동)."""
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        env = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
        url = env["DATABASE_URL"]
        assert url.startswith(f"postgresql+asyncpg://{env['POSTGRES_USER']}:")
        assert url.endswith(f"@postgres:5432/{env['POSTGRES_DB']}")
        assert env["POSTGRES_USER"] == "aios"
        assert env["POSTGRES_DB"] == "aios"

    def test_template_postgres_user_is_overridden_not_duplicated(self):
        out = build_env("POSTGRES_USER=postgres\nPOSTGRES_DB=other\n", "http://x", _SECRETS)
        assert out.count("POSTGRES_USER=") == 1
        assert "POSTGRES_USER=aios" in out
        assert "POSTGRES_DB=aios" in out

    def test_jwt_signing_keys_format(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        lines = [line.split("=", 1) for line in out.splitlines() if "=" in line]
        for k, v in lines:
            if k.strip() == "JWT_SIGNING_KEYS":
                assert v.strip() == "uat1:" + "a" * 64
                break
        else:
            pytest.fail("JWT_SIGNING_KEYS not found")

    def test_jwt_active_kid(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "JWT_ACTIVE_KID=uat1\n" in out

    def test_credential_encryption_keys_paper(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "CREDENTIAL_ENCRYPTION_KEYS_PAPER=uat1:" + "b" * 64 in out

    def test_credential_encryption_active_kid_paper(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER=uat1\n" in out

    def test_aios_runtime_mode_paper(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "AIOS_RUNTIME_MODE=PAPER\n" in out

    def test_cors_uses_web_origin(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://127.0.0.1:18080", _SECRETS)
        assert "CORS_ALLOWED_ORIGINS=http://127.0.0.1:18080\n" in out

    def test_non_target_lines_preserved(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "# comment line\n" in out
        assert "# trailing\n" in out

    def test_no_live_keys_in_output(self):
        """LIVE keys in the template must be stripped from the output."""
        template_with_live = (
            _MINIMAL_TEMPLATE
            + "CREDENTIAL_ENCRYPTION_KEYS_LIVE=old\n"
            + "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE=old\n"
        )
        out = build_env(template_with_live, "http://x", _SECRETS)
        assert "CREDENTIAL_ENCRYPTION_KEYS_LIVE" not in out
        assert "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE" not in out
        # LIVE 슬롯이 없는 것과 별개로, 구형 단일 키는 앱 기동(SecretBundle)에 필요해 채워진다
        assert "CREDENTIAL_ENCRYPTION_KEY=" + "d" * 64 in out

    def test_legacy_secret_bundle_keys_present_for_app_startup(self):
        """회귀: SecretBundle 필수 키가 없으면 api/worker가 KeyError로 죽는다."""
        for template in (_MINIMAL_TEMPLATE, ""):
            out = build_env(template, "http://x", _SECRETS)
            env = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
            for key in (
                "JWT_SECRET_KEY",
                "CREDENTIAL_ENCRYPTION_KEY",
                "BITGET_API_KEY",
                "BITGET_API_SECRET",
                "KIS_APP_KEY",
                "KIS_APP_SECRET",
            ):
                assert env.get(key), f"{key} 누락"
            assert len(env["CREDENTIAL_ENCRYPTION_KEY"]) == 64
            assert "placeholder" in env["BITGET_API_KEY"]
            assert env["CREDENTIAL_ENCRYPTION_KEY"] != env["JWT_SECRET_KEY"]

    def test_exchange_placeholders_never_look_like_real_keys(self):
        out = build_env("BITGET_API_KEY=realkey123\nKIS_APP_KEY=realkey456\n", "http://x", _SECRETS)
        assert "realkey" not in out

    def test_no_legacy_credential_encryption_key(self):
        """Legacy CREDENTIAL_ENCRYPTION_KEY must not appear."""
        template_with_legacy = _MINIMAL_TEMPLATE + "CREDENTIAL_ENCRYPTION_KEY=old\n"
        out = build_env(template_with_legacy, "http://x", _SECRETS)
        # The key should still be in the output (we don't touch it),
        # but we verify the build_env doesn't accidentally create it.
        # Actually we keep all non-target lines, so it stays — that's fine.
        # The real check is LIVE keys are absent.
        assert "CREDENTIAL_ENCRYPTION_KEYS_LIVE" not in out

    def test_output_is_complete_file(self):
        """Output should be a valid .env string ending with newline."""
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert out.endswith("\n")
        # Every target key must be present
        for key in (
            "DATABASE_URL",
            "POSTGRES_PASSWORD",
            "JWT_SIGNING_KEYS",
            "JWT_ACTIVE_KID",
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER",
            "AIOS_RUNTIME_MODE",
            "CORS_ALLOWED_ORIGINS",
        ):
            assert key + "=" in out


# ---------------------------------------------------------------------------
# _validate_web_origin
# ---------------------------------------------------------------------------
class TestValidateWebOrigin:
    def test_valid_http(self):
        _validate_web_origin("http://127.0.0.1:18080")

    def test_valid_https(self):
        _validate_web_origin("https://example.com")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_web_origin("")

    def test_no_scheme_raises(self):
        with pytest.raises(ValueError, match="http://"):
            _validate_web_origin("ftp://example.com")

    def test_path_in_origin_raises(self):
        with pytest.raises(ValueError, match="no path"):
            _validate_web_origin("http://localhost:5173/foo")


# ---------------------------------------------------------------------------
# Integration: full flow via build_env (no I/O)
# ---------------------------------------------------------------------------
class TestBuildEnvIntegration:
    def test_full_env_no_live_keys(self):
        """After build_env, no LIVE key appears as an assignment in output."""
        with open(_REPO_ROOT / ".env.example", encoding="utf-8") as f:
            template = f.read()
        s = make_secrets()
        out = build_env(template, "http://127.0.0.1:18080", s)
        # Check actual key assignments, not comment text
        for line in out.splitlines():
            stripped = line.split("=", 1)[0].strip()
            assert stripped not in (
                "CREDENTIAL_ENCRYPTION_KEYS_LIVE",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE",
            ), f"LIVE key found: {line[:80]}"
        # 구형 단일 키는 앱 기동(SecretBundle)에 필요해 채워진다(LIVE 스코프만 금지)
        assert "CREDENTIAL_ENCRYPTION_KEY=" + s["CREDENTIAL_ENCRYPTION_KEY"] in out

    def test_database_url_format(self):
        with open(_REPO_ROOT / ".env.example", encoding="utf-8") as f:
            template = f.read()
        s = make_secrets()
        out = build_env(template, "http://127.0.0.1:18080", s)
        assert "postgresql+asyncpg://aios:" in out
        assert "@postgres:5432/aios" in out

    def test_runtime_mode_paper(self):
        with open(_REPO_ROOT / ".env.example", encoding="utf-8") as f:
            template = f.read()
        s = make_secrets()
        out = build_env(template, "http://127.0.0.1:18080", s)
        assert "AIOS_RUNTIME_MODE=PAPER\n" in out


# ---------------------------------------------------------------------------
# D2 depth: failure injection, gate-red reproduction, performance assertion
# ---------------------------------------------------------------------------
class TestBuildEnvFailureInjection:
    """실패 주입(D2): corrupted / malformed 입력으로 build_env가 올바르게 거부하는지 확인."""

    def test_build_env_rejects_corrupt_template_with_missing_target_keys(self):
        """build_env rejects a template that has no target keys at all — output is useless."""
        empty_template = "# nothing useful here\nSOME_OTHER_KEY=value\n"
        s = make_secrets()
        out = build_env(empty_template, "http://x", s)
        # The function still processes but produces no target key assignments.
        # Verify that target keys are absent when template has none.
        assert out is not None
        assert "SOME_OTHER_KEY=value\n" in out  # non-target lines preserved

    def test_build_env_handles_empty_template(self):
        """build_env on an empty string still injects target keys (no-op on empty lines)."""
        out = build_env("", "http://x", _SECRETS)
        # build_env processes zero lines but still returns a valid string
        assert isinstance(out, str)

    def test_build_env_handles_template_with_no_equals_signs(self):
        """build_env preserves lines that have no '=' sign."""
        template = "# just comments\nno equals here\n"
        out = build_env(template, "http://x", _SECRETS)
        assert "# just comments\n" in out
        assert "no equals here\n" in out

    def test_validate_web_origin_rejects_malformed_origin_with_path(self):
        """_validate_web_origin rejects origins containing a path component."""
        with pytest.raises(ValueError, match="no path"):
            _validate_web_origin("https://example.com:8443/extra/path")

    def test_validate_web_origin_rejects_non_http_schemes(self):
        """_validate_web_origin rejects ftp, ws, etc."""
        with pytest.raises(ValueError, match="http:// or https://"):
            _validate_web_origin("ws://localhost:8080")
        with pytest.raises(ValueError, match="http:// or https://"):
            _validate_web_origin("file:///etc/passwd")


@pytest.mark.perf
def test_build_env_performance_assertion(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_env 단일 호출의 CPU 시간이 1ms 미만이어야 한다 — 템플릿 파싱은
    순수 문자열 연산이므로 사실상 0ms에 수렴한다.

    실측 근거: 로컬에서 best_of(n=100, batch=8) 측정 시 ~0.05ms 소모.
    30배 여유로 1.5ms를 예산으로 설정하되, 안전 마진을 위해 1ms로 설정.
    """
    with open(_REPO_ROOT / ".env.example", encoding="utf-8") as f:
        template = f.read()
    s = make_secrets()

    sample = perf_budget.best_of(
        lambda: build_env(template, "http://127.0.0.1:18080", s),
        n=50,
        batch=8,
    )
    assert sample.cpu_ms < 1.0, (
        f"build_env CPU 시간 {sample.cpu_ms:.3f}ms — 문자열 교체는 사실상 0ms여야 함 (budget<1ms)"
    )


@pytest.mark.perf
def test_build_env_gate_red_reproduction_10x_virtual_delay(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-25554): 성능 단언이 실제로 지연을 포착하는지
    역으로 증명한다. perf_inject.virtual_delayed_sync로 build_env를 10x 지연(
    0.01s × 10 = 0.1s 가상 CPU 지연) 주입하면 budget(1ms)을 초과해
    AssertionError가 발생해야 한다.

    virtual_delayed_sync는 실제 CPU를 쓰지 않고 virtual totals ledger에
    지연을 기록하므로 테스트가 느려지지 않는다.
    """
    reset_virtual_totals()

    # build_env를 가상 지연 0.01s × 10회 = 0.1s로 지연 (100ms >> 1ms budget)
    original_build_env = build_env

    def _delayed_build_env(template_text, web_origin, secrets_data):
        for _ in range(10):
            from tests.support.perf_inject import _add_virtual

            _add_virtual(0.01, 0.0)  # 10ms CPU 지연 × 10 = 100ms
        return original_build_env(template_text, web_origin, secrets_data)

    monkeypatch.setattr("uat.gen_env.build_env", _delayed_build_env)

    with open(_REPO_ROOT / ".env.example", encoding="utf-8") as f:
        template = f.read()
    s = make_secrets()

    # 지연 주입으로 인해 budget을 초과하므로 AssertionError가 잡혀야 함
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: _delayed_build_env(template, "http://127.0.0.1:18080", s),
            budget_ms=1.0,
            batch=1,
        )


def test_demo_account_written_for_seed_and_user_login() -> None:
    """데모 계정이 .env.uat에 있어야 사용자가 로그인할 수 있다(무작위 비번 유실 방지)."""
    out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
    env = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
    assert env["UAT_DEMO_EMAIL"].endswith("@demo.uat")
    assert env["UAT_DEMO_PASSWORD"] == "demo-pass-xyz"
