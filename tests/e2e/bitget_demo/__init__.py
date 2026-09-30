"""L4-30 DEEPEN(task-10086) -- negative/실패주입 보강.

원 리프 task-6704(고아 산출물 회수)가 패키지 마커만 남기고 비워 둔 파일이다.
`test_bitget_demo_credential_guard.py`는 `missing_demo_credentials()`(값
계산)만 실측한다 -- 이 파일은 그 함수의 소비자인
`skip_if_missing_demo_credentials()`(제어흐름: skip을 실제로 발생시키는지,
그리고 §10 정직 표기 원칙이 요구하는 redaction -- 실제 자격증명 값이 skip
사유 문자열에 새지 않는지)을 실측한다. 이 축은 `conftest.py`의
`demo_adapter` 픽스처가 간접 호출할 뿐 직접 단언한 적이 없다.
"""

from __future__ import annotations

import pytest
from _pytest.outcomes import Skipped

from tests.support.bitget_demo_credentials import (
    CREDENTIAL_ENV_VARS,
    missing_demo_credentials,
    skip_if_missing_demo_credentials,
)


def test_skip_if_missing_demo_credentials_skips_when_any_credential_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative #1 -- 자격증명 세 개 중 무엇이든 하나라도 없으면
    `skip_if_missing_demo_credentials()`는 조용히 통과하지 않고 실제로
    pytest skip을 발생시켜야 한다(그렇지 않으면 `demo_adapter`가 없는
    자격증명으로 `BitgetAdapter`를 구성해 인증 실패를 실 서버로 보낸다)."""
    key, secret, passphrase = CREDENTIAL_ENV_VARS
    monkeypatch.setenv(key, "aios-test-only-key")
    monkeypatch.setenv(secret, "aios-test-only-secret")
    monkeypatch.delenv(passphrase, raising=False)

    with pytest.raises(Skipped):
        skip_if_missing_demo_credentials()


def test_skip_if_missing_demo_credentials_does_not_skip_when_all_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative #2 -- 세 자격증명이 전부 있으면 skip을 발생시키지 않아야
    한다. 이 함수가 과도하게 fail-closed로 굴어 유효한 실행마저 skip으로
    삼키면 `demo_adapter`가 절대 실행되지 않는 죽은 코드가 된다."""
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(name, "aios-test-only-value")

    skip_if_missing_demo_credentials()  # 예외 없이 반환해야 한다.


def test_skip_reason_never_leaks_credential_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """negative #3 (redaction, §10 정직 표기 원칙) -- skip 사유 문자열은
    "어떤 변수가 없는지" 이름만 말하지, 설정된 다른 두 자격증명의 값은
    어떤 형태로도 포함하면 안 된다. 값이 새면 로그/CI 출력에 실키가
    찍히는 사고로 이어진다."""
    key, secret, passphrase = CREDENTIAL_ENV_VARS
    secret_value = "aios-test-only-secret-do-not-leak"
    monkeypatch.setenv(key, secret_value)
    monkeypatch.setenv(secret, secret_value)
    monkeypatch.delenv(passphrase, raising=False)

    with pytest.raises(Skipped) as exc_info:
        skip_if_missing_demo_credentials()

    reason = str(exc_info.value)
    assert secret_value not in reason
    assert passphrase in reason
    assert key not in reason
    assert secret not in reason


def test_missing_demo_credentials_propagates_when_environ_access_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입 -- `os.environ.get`이 예외를 던지면(예: 손상된 환경 상태)
    `missing_demo_credentials()`는 그 실패를 삼켜 빈 리스트(= "다 있음"으로
    오인)로 되돌리면 안 된다. 조용히 삼키면 `demo_adapter`가 없는
    자격증명을 "있다"고 착각해 인증 실패를 실 서버로 보낸다(fail-closed)."""
    import os

    def _boom(name: str, default: str | None = None) -> str | None:
        raise RuntimeError("environ access failed (injected)")

    monkeypatch.setattr(os.environ, "get", _boom)

    with pytest.raises(RuntimeError):
        missing_demo_credentials()
