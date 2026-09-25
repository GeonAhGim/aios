"""Unit tests for `src/foundation/ai/gateway/domain/confirm.py` -- task-2636
AI-2 DoD ("상승 불가·revoke 즉시·단일 사용" 중 단일 사용/TTL 부분)."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.ai.gateway.domain import confirm as confirm_module
from src.foundation.ai.gateway.domain.confirm import (
    ConfirmDigestMismatchError,
    ConfirmRuleError,
    ConfirmTicket,
    ConfirmTicketExpiredError,
    ConfirmTicketReusedError,
    is_consumed,
    is_expired,
    verify_and_consume,
)

_NOW = datetime(2026, 9, 17, 0, 0, 0, tzinfo=timezone.utc)
_DIGEST = "sha256:" + "ab" * 32


def _ticket(
    *,
    action_digest: str = _DIGEST,
    expires_at: datetime = _NOW + timedelta(minutes=5),
    consumed_at: datetime | None = None,
) -> ConfirmTicket:
    return ConfirmTicket(
        ticket_id=uuid.uuid4(),
        action_digest=action_digest,
        expires_at=expires_at,
        consumed_at=consumed_at,
    )


# --- verify_and_consume: 미리보기 digest == 실행 digest, 단일 사용, TTL ---


def test_verify_and_consume_returns_consumed_ticket_on_match() -> None:
    ticket = _ticket()
    consumed = verify_and_consume(ticket, _DIGEST, _NOW)
    assert consumed.consumed_at == _NOW
    assert is_consumed(consumed) is True
    assert is_consumed(ticket) is False  # 원본은 frozen -- 새 값을 반환할 뿐


def test_verify_and_consume_rejects_digest_mismatch() -> None:
    ticket = _ticket(action_digest=_DIGEST)
    with pytest.raises(ConfirmDigestMismatchError):
        verify_and_consume(ticket, "sha256:" + "cd" * 32, _NOW)


def test_verify_and_consume_rejects_expired_ticket() -> None:
    ticket = _ticket(expires_at=_NOW)
    with pytest.raises(ConfirmTicketExpiredError):
        verify_and_consume(ticket, _DIGEST, _NOW)


def test_verify_and_consume_rejects_reused_ticket() -> None:
    ticket = _ticket(consumed_at=_NOW - timedelta(seconds=1))
    with pytest.raises(ConfirmTicketReusedError):
        verify_and_consume(ticket, _DIGEST, _NOW)


def test_verify_and_consume_reuse_takes_priority_over_expiry() -> None:
    """검사 순서 계약: 이미 소비된 티켓은 그새 만료됐더라도 항상 "재사용"
    으로 거부해야 한다(§6) -- "소비는 됐지만 아직 안 만료됐으니 괜찮다"는
    우회, 또는 반대로 만료 메시지 뒤에 재사용 사실이 가려지는 것을 막는다."""
    ticket = _ticket(
        expires_at=_NOW - timedelta(seconds=1), consumed_at=_NOW - timedelta(seconds=1)
    )
    with pytest.raises(ConfirmTicketReusedError):
        verify_and_consume(ticket, _DIGEST, _NOW)


def test_confirm_ticket_rejects_empty_action_digest() -> None:
    with pytest.raises(ConfirmRuleError):
        _ticket(action_digest="")


def test_is_expired_boundary_is_inclusive() -> None:
    ticket = _ticket(expires_at=_NOW)
    assert is_expired(ticket, _NOW) is True
    assert is_expired(ticket, _NOW - timedelta(microseconds=1)) is False


# --- 실패 주입: 상류 데이터 손상이 조용히 통과하지 않고 fail-closed 거부 ---


def test_verify_and_consume_rejects_naive_datetime_expiry_from_upstream_corruption() -> None:
    """실패 주입: 상류가 tz-aware 규율(CLAUDE.md #3 "All datetimes are
    timezone-aware UTC")을 어기고 naive datetime을 `expires_at`에 채우면
    (예: 저장소 역직렬화가 UTC 오프셋을 빠뜨림), naive/aware datetime 비교는
    이미 TypeError를 내므로 "아직 안 만료됐다"로 조용히 통과시키지 않고
    fail-closed 거부해야 한다."""
    ticket = _ticket()
    object.__setattr__(ticket, "expires_at", datetime(2026, 9, 17, 0, 5))  # naive, 주입된 손상
    with pytest.raises(TypeError):
        verify_and_consume(ticket, _DIGEST, _NOW)


def test_verify_and_consume_rejects_non_string_digest_from_upstream_corruption() -> None:
    """실패 주입: 상류(제안 컴파일러/직렬화)가 손상되어 `action_digest`가
    문자열이 아니라 bytes로 섞여 들어오면, 실행 측이 여전히 str을 넘기는 한
    `!=` 비교 자체는 값이 다르므로 통과되지 않지만(파이썬은 타입이 달라도
    `!=`가 예외를 던지지 않는다), 이 테스트는 그 "다르면 그냥 mismatch"
    fail-closed 동작이 유지되는지(자동으로 어느 한쪽을 캐스팅해 몰래
    맞춰버리지 않는지) 못박는다."""
    ticket = _ticket(action_digest=_DIGEST)
    object.__setattr__(ticket, "action_digest", _DIGEST.encode("ascii"))  # 주입된 손상: bytes
    with pytest.raises(ConfirmDigestMismatchError):
        verify_and_consume(ticket, _DIGEST, _NOW)


# --- 수치 성능 단언: verify_and_consume() 핫 패스(promote_to_paper 확인 경로) ---
# §7 SLO에 확인 토큰 검증 전용 항목은 없다 -- CPU 전용(순수 비교) 구간이라는
# 사실 위에 자체 예산을 건다.

_VERIFY_BUDGET_MS = 1.0


def _verify_latencies_ms(iterations: int = 200) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        ticket = _ticket()
        started = time.perf_counter()
        verify_and_consume(ticket, _DIGEST, _NOW)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_verify_and_consume_p95_latency_within_self_declared_budget() -> None:
    samples = _verify_latencies_ms()
    p95_ms = _p95(samples)
    print(f"[AI-2 confirm] verify_and_consume p95={p95_ms:.4f}ms budget<{_VERIFY_BUDGET_MS:.1f}ms")
    assert p95_ms < _VERIFY_BUDGET_MS


def test_verify_and_consume_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: verify_and_consume()이 위임하는 `is_consumed`
    경로(호출당 1회)에 예산을 실제로 넘기는 지연을 주입했을 때 위 성능
    단언이 진짜로 AssertionError를 내는지 확인한다 -- 이 테스트가 없으면
    위 단언이 항상 통과하는 tautology인지 아무도 검증하지 못한다."""
    original_is_consumed = confirm_module.is_consumed

    def _stalled_is_consumed(ticket: ConfirmTicket) -> bool:
        time.sleep(_VERIFY_BUDGET_MS / 1000.0)
        return original_is_consumed(ticket)

    monkeypatch.setattr(confirm_module, "is_consumed", _stalled_is_consumed)

    samples = _verify_latencies_ms(iterations=5)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _VERIFY_BUDGET_MS
