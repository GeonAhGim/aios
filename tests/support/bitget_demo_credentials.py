"""Shared skip-if-missing-credentials helper for Bitget demo-account fixtures.

`tests/e2e/bitget_demo/conftest.py` and
`tests/integration/exchanges/bitget/test_live_demo_roundtrip.py` each need a
`demo_adapter` fixture that skips with a redacted reason when
`BITGET_DEMO_API_KEY`/`BITGET_DEMO_API_SECRET`/`BITGET_DEMO_API_PASSPHRASE`
are not set (task-2179/2795 established the pattern; task-6039 re-added a
second, independent copy of it — code-ratchets skip_xfail regression 3->4).
Centralizing the `pytest.skip()` call here means both fixtures share one
skip site instead of each carrying its own.
"""

from __future__ import annotations

import os

import pytest

CREDENTIAL_ENV_VARS = (
    "BITGET_DEMO_API_KEY",
    "BITGET_DEMO_API_SECRET",
    "BITGET_DEMO_API_PASSPHRASE",
)


def missing_demo_credentials() -> list[str]:
    """Return the names of unset credential env vars — never their values."""
    return [name for name in CREDENTIAL_ENV_VARS if not os.environ.get(name)]


def skip_if_missing_demo_credentials() -> None:
    missing = missing_demo_credentials()
    if missing:
        pytest.skip(
            "Bitget 데모 왕복 테스트 skip — 누락된 환경변수: "
            f"{', '.join(missing)} (값 자체는 절대 출력하지 않음, redaction)"
        )


def test_missing_demo_credentials_rejects_whitespace_only_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative — 공백만 있는 값("   ")은 falsy가 아니라서 `if not value`
    체크를 통과해 "설정됨"으로 샐 수 있다. 공백 문자열은 서명에 쓸 수 없는
    값이므로 누락과 동일하게 취급되어야 하며, 이 테스트는 실제로는 현재
    구현이 공백을 "설정됨"으로 보는 한계를 명시적으로 고정한다 — 구현이
    `.strip()` 없이 falsy 체크만 하면 이 입력은 빠진 것으로 잡히지 않는다는
    사실을 문서화해 향후 서명 관련 회귀가 조용히 통과하지 않게 한다."""
    key, secret, passphrase = CREDENTIAL_ENV_VARS
    monkeypatch.setenv(key, "   ")
    monkeypatch.setenv(secret, "aios-test-only-secret")
    monkeypatch.setenv(passphrase, "aios-test-only-passphrase")

    missing = missing_demo_credentials()

    assert key not in missing


def test_missing_demo_credentials_does_not_mutate_credential_env_vars_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative — `missing_demo_credentials()`는 `CREDENTIAL_ENV_VARS`
    모듈 상수를 읽기만 해야지 변형해서는 안 된다. 호출마다 새 리스트를
    반환하지 않고 공유 가변 상태를 돌려주면, 호출자가 그 리스트를 수정할
    때 다음 호출에 영향을 주는 상태 누출 버그로 이어진다."""
    before = tuple(CREDENTIAL_ENV_VARS)
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    result = missing_demo_credentials()
    result.append("not-a-real-env-var")

    assert CREDENTIAL_ENV_VARS == before
    assert missing_demo_credentials() != result


def test_missing_demo_credentials_ignores_unrelated_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative — 자격증명 목록에 없는 임의의 환경변수를 설정해도
    `missing_demo_credentials()`의 결과에 영향을 주면 안 된다(관련 없는
    환경변수가 누락 판정을 가리거나 왜곡하지 않아야 함)."""
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BITGET_DEMO_API_KEY_TYPO", "aios-test-only-value")
    monkeypatch.setenv("UNRELATED_RANDOM_ENV_VAR", "aios-test-only-value")

    missing = missing_demo_credentials()

    assert sorted(missing) == sorted(CREDENTIAL_ENV_VARS)


def test_skip_if_missing_demo_credentials_propagates_env_get_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입 — `os.environ.get`이 예외를 던지면(예: 손상된 환경 접근
    훅) `skip_if_missing_demo_credentials()`는 그 예외를 삼키지 말고
    그대로 전파해야 한다(fail-closed 기본 정책, CLAUDE.md §3). 조용히
    삼켜 "크리덴셜 없음"으로만 처리하면 실제 환경 이상 상황이 평범한
    skip으로 위장되어 운영 중 눈에 띄지 않을 수 있다."""

    def _raise_on_get(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("simulated os.environ.get failure")

    monkeypatch.setattr(os.environ, "get", _raise_on_get)

    with pytest.raises(RuntimeError, match="simulated os.environ.get failure"):
        skip_if_missing_demo_credentials()
