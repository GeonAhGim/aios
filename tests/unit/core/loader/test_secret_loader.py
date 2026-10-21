import pytest

import src.core.loader.secret_loader as secret_loader_module
from src.core.loader.secret_loader import load_env_secrets
from tests.conftest import PerfBudget

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://user:password@localhost:5432/aios_dev",
    "JWT_SECRET_KEY": "test-secret",
    "CREDENTIAL_ENCRYPTION_KEY": "test-key",
    "BITGET_API_KEY": "bk",
    "BITGET_API_SECRET": "bs",
    "KIS_APP_KEY": "kk",
    "KIS_APP_SECRET": "ks",
}


def test_load_env_secrets_from_explicit_source():
    bundle = load_env_secrets(MINIMAL_ENV)

    assert bundle.database_url.get_secret_value() == MINIMAL_ENV["DATABASE_URL"]
    assert bundle.jwt_algorithm == "HS256"  # 기본값
    assert bundle.jwt_expire_minutes == 60  # 기본값
    assert bundle.smtp_host is None


def test_load_env_secrets_optional_fields_populated():
    env = {**MINIMAL_ENV, "SMTP_HOST": "smtp.example.com", "JWT_EXPIRE_MINUTES": "120"}
    bundle = load_env_secrets(env)

    assert bundle.smtp_host == "smtp.example.com"
    assert bundle.jwt_expire_minutes == 120


def test_load_env_secrets_missing_required_key_raises():
    incomplete = dict(MINIMAL_ENV)
    del incomplete["JWT_SECRET_KEY"]
    with pytest.raises(KeyError):
        load_env_secrets(incomplete)


def test_load_env_secrets_missing_bitget_secret_raises():
    """negative — 경계값: 다른 필수 키(BITGET_API_SECRET)가 빠진 경우도
    동일하게 KeyError로 fail-closed 해야 한다 (단일 키 누락 케이스에만
    의존하지 않기 위한 추가 경계 검증)."""
    incomplete = dict(MINIMAL_ENV)
    del incomplete["BITGET_API_SECRET"]
    with pytest.raises(KeyError):
        load_env_secrets(incomplete)


def test_load_env_secrets_non_numeric_jwt_expire_minutes_raises():
    """negative — 잘못된 형식: JWT_EXPIRE_MINUTES가 정수로 파싱 불가능한
    문자열이면 조용히 기본값으로 폴백하지 않고 ValueError로 실패해야
    한다(fail-closed)."""
    env = {**MINIMAL_ENV, "JWT_EXPIRE_MINUTES": "not-a-number"}
    with pytest.raises(ValueError):
        load_env_secrets(env)


def test_load_env_secrets_non_numeric_smtp_port_raises():
    """negative — 잘못된 형식: SMTP_PORT가 정수로 파싱 불가능하면
    ValueError로 실패해야 한다."""
    env = {**MINIMAL_ENV, "SMTP_PORT": "not-a-port"}
    with pytest.raises(ValueError):
        load_env_secrets(env)


def test_load_env_secrets_repr_never_leaks_values():
    bundle = load_env_secrets(MINIMAL_ENV)
    assert "test-secret" not in repr(bundle)


def test_load_env_secrets_model_dump_never_leaks_values():
    """docs/RED_TEAM_FINDINGS.md #10 회귀 — __repr__/__str__만 마스킹해서는
    FastAPI가 실제로 쓰는 model_dump()/model_dump_json() 경로를 그대로
    우회해 평문을 반환했다. SecretStr로 바꾼 뒤에는 이 경로도 마스킹돼야
    한다."""
    bundle = load_env_secrets(MINIMAL_ENV)

    dumped = bundle.model_dump()
    dumped_json = bundle.model_dump_json()

    for value in MINIMAL_ENV.values():
        assert value not in repr(dumped)
        assert value not in dumped_json


def test_load_env_secrets_merges_dotenv_and_os_environ(monkeypatch: pytest.MonkeyPatch):
    """source=None 경로(_merged_environment) — os.environ이 .env 파일 값을
    덮어써야 한다(실제 배포 환경변수가 로컬 .env보다 우선)."""
    monkeypatch.setattr(
        secret_loader_module,
        "dotenv_values",
        lambda path: {**MINIMAL_ENV, "JWT_ALGORITHM": "from-dotenv"},
    )
    monkeypatch.setattr(
        secret_loader_module.os,
        "environ",
        {"JWT_ALGORITHM": "from-os-environ"},
    )

    bundle = load_env_secrets()

    assert bundle.jwt_algorithm == "from-os-environ"


def test_load_env_secrets_propagates_dotenv_read_failure(monkeypatch: pytest.MonkeyPatch):
    """failure injection — 의존성(dotenv_values)이 파일 I/O 오류 등으로
    예외를 던지면 삼키거나 빈 설정으로 폴백하지 않고 그대로 전파해야
    한다(fail-closed)."""

    def _boom(path):
        raise OSError("dotenv read failed")

    monkeypatch.setattr(secret_loader_module, "dotenv_values", _boom)

    with pytest.raises(OSError):
        load_env_secrets()


@pytest.mark.perf
def test_load_env_secrets_repeated_calls_stay_within_perf_budget(perf_budget: PerfBudget):
    """성능 단언 — load_env_secrets는 부팅 경로에서 반복 호출될 수 있으니
    (헬스체크·워커 재기동 등) 단일 호출 비용이 병적으로 커지면 안 된다."""
    # Preserve the original 2-second budget for 500 calls in per-call milliseconds.
    # Batch fast calls so CPU timer quantization is amortized across the sample.
    perf_budget.assert_within(
        lambda: load_env_secrets(MINIMAL_ENV), budget_ms=2.0 * 1000 / 500, batch=20000
    )


@pytest.mark.perf
def test_load_env_secrets_cpu_budget_goes_red_with_10x_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    예산(4.0ms × 10 = 40ms)을 초과하는 CPU 시간을 가장하면 budget 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다(task-11539 감사: 호출 1회 CPU 측정의 0 수렴 결함 방지).
    """
    budget_ms = 2.0 * 1000 / 500  # 동일 예산 (~4.0ms)

    def _run() -> object:
        return load_env_secrets(MINIMAL_ENV)

    # process_time을 monkeypatch: 호출할 때마다 40ms를 더한다.
    # PerfBudget.sample()은 time.process_time()을 호출하므로 이 값이
    # cpu_ms 측정값에 그대로 반영돼 budget을 10배 초과한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 40.0  # 40ms = 10x budget per call
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            _run,
            budget_ms=budget_ms,
            n=5,
            warmup=0,
            batch=1,
            label="secret_loader budget < 4.0ms (injection)",
        )
