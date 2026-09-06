"""직무분리(Segregation of Duty) 원시타입 — PLT-43.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-43,
ADR-2026-09-06-G §9 ("RBAC이 5역할 평면이고 '작성자≠승인자'가 기능마다 따로
구현된다 — Charles River는 기능단위 권한 격자를 쓴다").

이 모듈은 "actor != counterparty_for(action)"이라는 단일 불변조건의 유일한
정의다. 어떤 기능(mandate 규칙번들 거버넌스인 CM-5, break-glass 승인인
PLT-35, 그 밖의 승인·집행 경로)이든 "요청자 본인이 자신의 요청을 승인·집행"
하려는 시도는 여기를 거쳐 거부해야 한다 — 각 기능이 `if a == b: raise ...`를
따로 재발명하지 않는다.

미검증: 2026-09-07 기준 이 저장소에는 CM-5(`mandates/application/
activate_revision.py`·`propose_amendment.py`의 작성자≠승인자 강제)와
PLT-35(`core/security/break_glass.py`)가 아직 구현되어 있지 않다 — 둘 다
아직 다른 리프로도 존재하지 않는 스펙상의 계획일 뿐이다. 오늘 실제로 이
원시타입으로 옮겨 배선한 것은 `core/approval/service.py`의 DUAL 모드
2차 서명 검사(기존에 인라인으로 재발명돼 있었다)뿐이다.
`tests/foundation/unit/trust/test_segregation_of_duty_static.py`의 정적
스캔은 이 사실을 반영해, "이 원시타입 밖에서 actor/counterparty 동일성을
직접 비교하는 코드가 없다"만 오늘 시점 진실로 단언한다 — CM-5·PLT-35가
나중에 만들어질 때 이 모듈을 거치지 않고 인라인으로 재발명하면 그 즉시
이 정적 검사가 잡아낸다.
"""
from __future__ import annotations

from collections.abc import Hashable


class SegregationOfDutyViolation(Exception):
    """동일한 주체가 어떤 action의 actor이자 counterparty로 행동하려 했다."""

    def __init__(self, actor_id: Hashable, action: str) -> None:
        super().__init__(
            f"{action}: actor({actor_id!r})는 자기 자신의 counterparty가 될 수 없습니다."
        )
        self.actor_id = actor_id
        self.action = action


def assert_actor_not_counterparty(
    actor_id: Hashable, counterparty_id: Hashable | None, *, action: str
) -> None:
    """actor_id가 이 action의 counterparty_id와 같으면 거부한다.

    `counterparty_id`가 `None`이면(아직 아무도 그 역할을 맡지 않은 상태 —
    예: DUAL 승인의 첫 서명 전, mandate 초안 제안 전) 비교할 대상이 없으므로
    그대로 통과시킨다. 두 id의 동등성은 `==`로 판정하므로 `UUID`·`str`·`int`
    등 값 동등성을 지원하는 어떤 식별자 타입에도 그대로 쓸 수 있다.
    """
    if counterparty_id is not None and actor_id == counterparty_id:
        raise SegregationOfDutyViolation(actor_id, action)


__all__ = ["SegregationOfDutyViolation", "assert_actor_not_counterparty"]
