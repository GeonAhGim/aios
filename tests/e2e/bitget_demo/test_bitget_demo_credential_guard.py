"""L4-30 — `tests/e2e/bitget_demo/` 크리덴셜 가드 + `verified` 레드게이트.

`test_bitget_demo_pipeline.py`의 세 테스트는 전부 `live_demo` 마커라
`BITGET_DEMO_API_KEY`/`SECRET`/`PASSPHRASE`가 없는 이 환경(그리고 기본
CI)에서는 skip으로 끝난다 — 이 파일은 그 skip 자체와, 아직 실키 왕복이
한 번도 통과하지 못했다는 상태(`BITGET_SPOT_PROFILE.verified`)를
마커 없이 항상 실행되는 테스트로 고정한다(빈 통과로 리프를 끝내지
않기 위함, §10 정직 표기 원칙).

레드게이트 재현(D3) — ADR-2026-09-06-G §11은 "place/cancel/get 왕복 AND
잘못된 주문 거부가 둘 다 실제로 통과한 커밋에서만" `verified`를
`"LIVE_VERIFIED"`로 올리라고 못박는다. `test_verified_flag_stays_doc_only_
without_a_live_pass_on_record`는 이 규칙을 어기고 값만 올린 커밋이
들어오면 즉시 빨갛게 실패해, ND-17이 발견한 "실재하지 않는 산출물을
done으로 표기"와 같은 종류의 사고(여기서는 검증 없이 verified만 올리는
사고)를 이 leaf 안에서 재현·차단한다.
"""
from __future__ import annotations

import pytest

from src.exchanges.bitget.venue_profile import BITGET_SPOT_PROFILE
from tests.e2e.bitget_demo.conftest import CREDENTIAL_ENV_VARS, missing_demo_credentials


def test_missing_all_credentials_reports_every_variable_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    missing = missing_demo_credentials()

    assert sorted(missing) == sorted(CREDENTIAL_ENV_VARS)


def test_partial_credentials_still_report_as_missing_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative — 크리덴셜 3개 중 2개만 있어도 "준비됨"으로 새지 않는다.
    부분 설정을 완전 설정으로 착각하면 인증 실패로 실제 Bitget 서버에
    잘못된 서명을 보내는 사고(2795 DEEPEN이 고친 바로 그 결함 클래스)로
    이어진다."""
    key, secret, passphrase = CREDENTIAL_ENV_VARS
    monkeypatch.setenv(key, "aios-test-only-partial-key")
    monkeypatch.setenv(secret, "aios-test-only-partial-secret")
    monkeypatch.delenv(passphrase, raising=False)

    missing = missing_demo_credentials()

    assert missing == [passphrase]


def test_all_credentials_present_reports_no_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(name, "aios-test-only-value")

    assert missing_demo_credentials() == []


def test_verified_flag_stays_doc_only_without_a_live_pass_on_record() -> None:
    """red-gate reproduction — 이 커밋에는 실키가 없어 `test_bitget_demo_
    pipeline.py`의 왕복 테스트가 한 번도 실제로 통과하지 못했다. 따라서
    `verified`는 반드시 `"DOC_ONLY"`로 남아 있어야 한다 — 값이 조용히
    `"LIVE_VERIFIED"`로 바뀐다면(검증 없이) 이 테스트가 즉시 실패해 잡는다
    (ADR-2026-09-06-G §11)."""
    assert BITGET_SPOT_PROFILE.verified == "DOC_ONLY"
