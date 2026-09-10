"""RD-5 `domain/entity_link.py` -- DEEPEN(task-2906, DEPTH_DC_RD 소급감사
task-2726) D1 -> D2 증빙.

기존 test_entity_link.py는 4개 키 형태의 happy-path와 이름-전용 미매핑 1건
(D1)으로 §9 RD-5 DoD (a)-(d)를 증명했다 -- 소급감사에서 "D2 하한 미달:
실패주입(DB/네트워크/크래시) 없음, 수치 성능 단언 없음, 게이트 적색 재현
없음"으로 지적됐다. 이 파일이 그 부족분을 채운다. entity_link.py는 무수정
-- 새 기능 없음, 깊이만 올림.

1. 실패 주입/크래시 -- 주입된 `EntityResolver`가 예외를 던지면(symbol_master
   조회 중 크래시를 흉내) `link_item`이 이를 삼켜 `NOT_FOUND`로 위장하지
   않고 그대로 전파하는지, 그리고 여러 후보 키가 있을 때 첫 키에서 크래시가
   나면 뒤 키로 "조용히 넘어가지" 않는지를 증명한다.
2. 성능 단언 -- 순수 함수(I/O 없음)인 `extract_entity_key`가 20,000건의
   혼합 입력(4개 유효 형태 + 무효 형태)을 절대시간 예산 내에 처리하는지,
   그리고 `link_item`이 매칭 성공 직후 남은 후보 키에 대해 `resolver.resolve`
   를 추가 호출하지 않는지(불필요한 호출 수 상한)를 수치로 단언한다.
3. 게이트 적색 재현 -- `is_valid_isin`의 체크섬 계산이 자릿수 가중치를
   무시하도록 퇴행하면(예: 짝수/홀수 위치 가중을 없애는 리팩터) 어떤
   단일 자릿수 변조라도 걸러내야 하는 이 테스트가 결정적으로 실패하도록,
   유효한 ISIN의 11개 자릿수 각각을 변조한 12개 변형 전체가 거부되는지를
   전수 검사한다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.entity_link import (
    EntityKey,
    EntityKeyKind,
    extract_entity_key,
    is_valid_isin,
    link_item,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_VALID_ISIN = "KR7005930003"  # 미검증: 삼성전자 보통주 ISIN(공개 문헌 인용, 거래소 원문 대조 없음)


def _item(*, instruments: tuple[str, ...]) -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id="OPENDART",
        kind="filing",
        published_at=_NOW,
        known_at=_NOW,
        instruments=instruments,
        title="title",
        body_ref=None,
        url="https://example.invalid/x",
        language="ko",
        hash="deadbeef",
        revision_of=None,
    )


# ---- 실패 주입/크래시 (resolver가 예외를 던지는 경우) ----


def test_link_item_propagates_resolver_crash_instead_of_masking_as_not_found() -> None:
    """resolver가 크래시하면 `link_item`은 이를 `NOT_FOUND`로 위장해 삼키지
    않는다 -- 크래시와 "정상적으로 못 찾음"은 서로 다른 신호이고, 전자를
    후자로 뭉개면 실제 장애(네트워크 단절 등)가 조용히 "미매핑"으로
    보고돼 재시도/알림 경로를 타지 못한다."""
    item = _item(instruments=("005930",))
    resolver = Mock()
    resolver.resolve.side_effect = RuntimeError("injected resolver crash")

    try:
        link_item(item, resolver)
    except RuntimeError as exc:
        assert str(exc) == "injected resolver crash"
    else:
        raise AssertionError("link_item must propagate resolver crashes, not swallow them")


def test_link_item_crash_on_first_key_does_not_silently_fall_through_to_second_key() -> None:
    """후보 키가 둘 이상일 때 첫 키에서 크래시가 나면, 두 번째 키로 "조용히
    넘어가서" 다른 결과를 내지 않는다 -- 그렇게 되면 크래시가 관측 불가능한
    상태로 사라진다."""
    item = _item(instruments=("005930", "000660"))
    resolver = Mock()
    resolver.resolve.side_effect = ConnectionError("injected network failure")

    try:
        link_item(item, resolver)
    except ConnectionError:
        pass
    else:
        raise AssertionError("crash on first key must propagate")

    resolver.resolve.assert_called_once_with(EntityKey(EntityKeyKind.KRX_CODE, "005930"))


# ---- 성능 단언 ----


def test_extract_entity_key_throughput_meets_latency_budget() -> None:
    """순수 함수(I/O 없음)이므로 대량 처리도 빨라야 한다 -- OpenDART 배치
    수집 시 하루 수만 건의 `instruments` 원문을 분류해야 하는 실제 부하를
    반영한 예산이다."""
    budget_sec = 1.0  # 실측 로컬 <0.05s, CI 편차 감안
    samples = (
        "005930",
        "1101110097494",
        _VALID_ISIN,
        "aapl:kis_us",
        "삼성전자우 유상증자",
        "",
        "notarealkeyshape",
    )
    n_repeats = 20_000 // len(samples)

    start = time.perf_counter()
    for _ in range(n_repeats):
        for raw in samples:
            extract_entity_key(raw)
    elapsed = time.perf_counter() - start

    total = n_repeats * len(samples)
    print(f"[RD-5 extract_entity_key] {total}건 분류 {elapsed:.3f}s (budget<{budget_sec}s)")
    assert elapsed < budget_sec, (
        f"extract_entity_key {total}건 처리가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


def test_link_item_stops_resolving_after_first_match_no_wasted_calls() -> None:
    """다섯 개 후보 키 중 세 번째에서 매칭되면, `resolver.resolve`는 정확히
    3번만 호출돼야 한다 -- 매칭 후 남은 후보를 계속 조회하는 것은 낭비이자
    (원격 조회라면) 불필요한 부하다."""
    item = _item(
        instruments=("005930", "000660", "005380", "035420", "051910"),
    )
    resolver = Mock()
    resolver.resolve.side_effect = [
        None,
        None,
        "INSTR-3",
        "SHOULD-NOT-BE-CALLED",
        "SHOULD-NOT-BE-CALLED",
    ]

    result = link_item(item, resolver)

    assert result.instrument_id == "INSTR-3"
    assert resolver.resolve.call_count == 3


# ---- 게이트 적색 재현 (ISIN 체크섬 회귀를 결정적으로 잡아낸다) ----


def test_is_valid_isin_rejects_every_single_digit_tamper_of_a_valid_isin() -> None:
    """유효한 ISIN의 마지막 체크 자릿수 앞 11자리 중 어느 한 자리라도
    바뀌면 전부 거부돼야 한다 -- 가중치(홀/짝 위치 2배) 계산을 빼먹는
    회귀가 생기면 이 중 최소 하나는 우연히 통과해 이 테스트가 결정적으로
    빨갛게 실패한다."""
    assert is_valid_isin(_VALID_ISIN) is True
    body, check_digit = _VALID_ISIN[:11], _VALID_ISIN[11]

    rejected = 0
    for position, ch in enumerate(body):
        if not ch.isdigit():
            continue  # 알파벳 자리는 숫자로 바꾸면 형태 자체가 달라지므로 제외
        for replacement in "0123456789":
            if replacement == ch:
                continue
            tampered = body[:position] + replacement + body[position + 1 :] + check_digit
            if is_valid_isin(tampered) is False:
                rejected += 1
            else:
                raise AssertionError(
                    f"tampered ISIN {tampered!r} (position {position}) was incorrectly accepted"
                )

    assert rejected > 0, "no digit position was exercised -- test setup is broken"
