"""FND-03 Audit Event — negative / 실패주입 테스트 (DB 불필요).

tests/foundation/integration/evidence/__init__.py 는 pytest가 수집하지 않으므로
(b15746a8c) DEEPEN 증빙은 수집되는 sibling 파일에 둔다.

DoD: negative test 3건 이상(불변식 위반 입력을 명시적으로 거부),
실패주입 1건 이상(monkeypatch로 의존성 예외 유발).
"""

from uuid import uuid4

import pytest

from src.foundation.evidence.application.append_audit_event import append_audit_event
from src.foundation.evidence.contracts.v1 import Classification, Outcome, RecordAuditEventCommand
from src.foundation.evidence.domain.rules import UnsafePayloadError


def _command(**overrides) -> RecordAuditEventCommand:
    defaults = dict(
        tenant_id=str(uuid4()),
        aggregate_type="mandate_revision",
        aggregate_id=str(uuid4()),
        aggregate_revision=1,
        action="mandate_activated",
        outcome=Outcome.SUCCESS,
        actor_subject_id=str(uuid4()),
        trace_id=str(uuid4()),
        payload={"revision_no": 1},
        classification=Classification.INTERNAL,
    )
    defaults.update(overrides)
    return RecordAuditEventCommand(**defaults)


class _RecordingRepo:
    """append_event가 호출되었는지 기록하는 최소 repository 대역."""

    def __init__(self) -> None:
        self.calls = 0

    async def append_event(self, **kwargs):  # pragma: no cover - 호출되면 실패해야 함
        self.calls += 1
        raise AssertionError("append_event must not be reached for unsafe payloads")


# ── negative tests (불변식 위반 입력을 명시적으로 거부) ──────────────────


@pytest.mark.parametrize("unsafe_key", ["password", "secret", "api_key", "token"])
async def test_negative_unsafe_payload_key_rejected_before_persist(unsafe_key):
    """AUD-006: payload에 민감 키가 있으면 UnsafePayloadError를 던지고 저장소에 닿지 않는다."""
    repo = _RecordingRepo()
    with pytest.raises(UnsafePayloadError):
        await append_audit_event(repo, _command(payload={unsafe_key: "value"}))
    assert repo.calls == 0


# ── failure injection (의존성 예외 유발) ────────────────────────────────


async def test_failure_injection_repository_error_propagates(monkeypatch):
    """실패주입: repository.append_event가 예외를 던지면 호출자에게 그대로 전파된다."""
    repo = _RecordingRepo()

    async def _db_down(**kwargs):
        raise RuntimeError("db connection lost")

    monkeypatch.setattr(repo, "append_event", _db_down)

    with pytest.raises(RuntimeError, match="db connection lost"):
        await append_audit_event(repo, _command())
