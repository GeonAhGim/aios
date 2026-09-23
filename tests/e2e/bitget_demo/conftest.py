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

CREDENTIAL_ENV_VARS = (
    "BITGET_DEMO_API_KEY",
    "BITGET_DEMO_API_SECRET",
    "BITGET_DEMO_API_PASSPHRASE",
)


def missing_demo_credentials() -> list[str]:
    """어느 크리덴셜 환경변수가 비어 있는지만 이름으로 반환한다 — 값은
    절대 반환/로그하지 않는다."""
    return [name for name in CREDENTIAL_ENV_VARS if not os.environ.get(name)]


@pytest.fixture
async def demo_adapter() -> AsyncGenerator[BitgetAdapter]:
    missing = missing_demo_credentials()
    if missing:
        pytest.skip(
            "Bitget 데모 e2e 테스트 skip — 누락된 환경변수: "
            f"{', '.join(missing)} (값 자체는 절대 출력하지 않음, redaction)"
        )
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
