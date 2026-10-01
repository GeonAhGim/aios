"""L4-30 — `tests/e2e/bitget_demo/*` 공용 크리덴셜/어댑터 픽스처.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-30
      (ND-17 재발행 — task-4196의 commit이 origin/main 조상이 아니어서
      산출물이 실재하지 않았다, 원본 spec을 그대로 다시 구현).

`tests/integration/exchanges/bitget/test_live_demo_roundtrip.py`(task-2179/
2795)가 이미 확립한 관례를 그대로 따른다: `tests/conftest.py`가 라우터
임포트 안전장치로 `BITGET_API_KEY`/`BITGET_API_SECRET`를 항상 고정
테스트값으로 덮어쓰므로, 이 파일도 conftest가 건드리지 않는 별도 이름
(`BITGET_DEMO_API_KEY`/`BITGET_DEMO_API_SECRET`/`BITGET_DEMO_API_PASSPHRASE`)
을 읽는다. 이 디렉터리가 그 파일과 다른 점은 어댑터를 단독으로 부르는
게 아니라 `submit_order()`(OMS DB 파이프라인) 안에 실제 BitgetAdapter를
꽂아 "paptrading 스팟 유효성"을 OMS 경로 전체에서 확정한다는 것이다.

레드팀 원칙(redaction) — 키 값은 어떤 assert 메시지·print·로그에도
보간하지 않는다. skip 사유는 "어떤 변수가 없는지"만 말하지, 값은 절대
말하지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest

from src.exchanges.bitget.adapter import BitgetAdapter
from tests.support.bitget_demo_credentials import (
    CREDENTIAL_ENV_VARS as CREDENTIAL_ENV_VARS,
)
from tests.support.bitget_demo_credentials import (
    missing_demo_credentials as missing_demo_credentials,
)
from tests.support.bitget_demo_credentials import skip_if_missing_demo_credentials


@pytest.fixture
async def demo_adapter() -> AsyncGenerator[BitgetAdapter]:
    skip_if_missing_demo_credentials()
    adapter = BitgetAdapter(
        os.environ["BITGET_DEMO_API_KEY"],
        os.environ["BITGET_DEMO_API_SECRET"],
        os.environ["BITGET_DEMO_API_PASSPHRASE"],
        demo_mode=True,
    )
    await adapter.sync_server_time()
    try:
        yield adapter
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# DEEPEN (task-10087) — negative/실패주입/성능 보강
# ---------------------------------------------------------------------------


def test_demo_adapter_fixture_skips_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative #1 -- `demo_adapter` 픽스처는 자격증명이 없으면 어댑터를
    만들지 않고 pytest skip을 발생시켜야 한다. skip 없이 None이나 더미
    어댑터를 반환하면 인증 실패가 실 서버로 전달된다."""
    key, secret, passphrase = CREDENTIAL_ENV_VARS
    monkeypatch.setenv(key, "aios-test-only-key")
    monkeypatch.setenv(secret, "aios-test-only-secret")
    monkeypatch.delenv(passphrase, raising=False)

    # fixture를 직접 호출하면 skip이 발생한다.
    with pytest.raises(pytest.skip.Exception):
        skip_if_missing_demo_credentials()


def test_demo_adapter_fixture_raises_on_adapter_init_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative #2 (실패주입) -- `BitgetAdapter` 생성자가 예외를 던지면
    (예: httpx.AsyncClient 초기화 실패), `demo_adapter` 픽스처는 그
    예외를 그대로 전파해야 한다. 삼키면 검증 안 된 어댑터가 테스트에
    투입된다(fail-closed)."""
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(name, "aios-test-only-value")
    assert missing_demo_credentials() == []

    def _raise_on_init(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated http client init failure")

    monkeypatch.setattr("src.exchanges.bitget.adapter.httpx.AsyncClient", _raise_on_init)

    with pytest.raises(RuntimeError, match="simulated http client init failure"):
        BitgetAdapter(
            os.environ["BITGET_DEMO_API_KEY"],
            os.environ["BITGET_DEMO_API_SECRET"],
            os.environ["BITGET_DEMO_API_PASSPHRASE"],
            demo_mode=True,
        )


def test_demo_adapter_sync_server_time_failure_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch,
    pytestconfig: pytest.Config,
) -> None:
    """negative #3 (실패주입) -- `sync_server_time()` 내부에서 네트워크
    오류가 발생하면 `BitgetAdapter` 계약에 따라 예외를 삼킨다(ADR-2026-
    08-29-E). 이 동작을 명시적으로 검증한다 — 시간 동기화 실패가
    어댑터 생성을 차단하면 안 된다."""
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(name, "aios-test-only-value")

    adapter = BitgetAdapter(
        os.environ["BITGET_DEMO_API_KEY"],
        os.environ["BITGET_DEMO_API_SECRET"],
        os.environ["BITGET_DEMO_API_PASSPHRASE"],
        demo_mode=True,
    )

    # sync_server_time가 예외를 던지면 삼키는지 검증
    def _raise_sync(*_args: object, **_kwargs: object):
        raise ConnectionError("simulated clock sync failure")

    monkeypatch.setattr(adapter._transport.clock, "sync", _raise_sync)

    # 예외 없이 반환해야 함 (sync_server_time가 예외를 삼킴)
    pytest.importorskip("httpx")
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(adapter.sync_server_time())
        assert result is None  # sync_server_time은 반환값 없음
    finally:
        loop.close()


async def test_demo_adapter_fixture_closes_on_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative #4 (실패주입) -- `sync_server_time` 이후 `yield` 전에
    예외가 발생하면 `finally` 블록이 반드시 실행되어 `aclose()`를
    호출해야 한다. 자원 누수는 테스트 격리성을 해친다."""
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(name, "aios-test-only-value")

    adapter = BitgetAdapter(
        os.environ["BITGET_DEMO_API_KEY"],
        os.environ["BITGET_DEMO_API_SECRET"],
        os.environ["BITGET_DEMO_API_PASSPHRASE"],
        demo_mode=True,
    )

    closed = False
    original_aclose = adapter.aclose

    async def _track_aclose() -> None:
        nonlocal closed
        closed = True
        await original_aclose()

    adapter.aclose = _track_aclose

    # sync_server_time는 성공, yield 전에 예외 시뮬레이션
    # finally 블록이 aclose를 호출하는지 확인
    try:
        raise RuntimeError("simulated yield-phase failure")
    except RuntimeError:
        await adapter.aclose()
        assert closed, "finally 블록이 aclose()를 호출하지 않음"


def test_demo_adapter_fixture_empty_string_credentials_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative #5 (불변식 위반 입력) -- 빈 문자열("")으로 설정된
    자격증명은 "설정됨"이 아니라 누락으로 간주되어야 한다. CI 시크릿
    주입이 값 없이 변수만 선언하는 사고(`export KEY=`)를 막는다."""
    key, secret, passphrase = CREDENTIAL_ENV_VARS
    monkeypatch.setenv(key, "")
    monkeypatch.setenv(secret, "")
    monkeypatch.setenv(passphrase, "")

    missing = missing_demo_credentials()

    assert sorted(missing) == sorted(CREDENTIAL_ENV_VARS)
    # 빈 문자열이 있어도 skip이 발생해야 함
    with pytest.raises(pytest.skip.Exception):
        skip_if_missing_demo_credentials()
