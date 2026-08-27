"""전역 싱글톤 접근자.

Spec: 05_communication_architecture_v1.2.md#§5.2 ("글로벌 싱글톤 접근자")

10번 문서 폴더트리에는 별도 파일로 명시돼 있지 않지만, 05번 문서 본문이
"src/core/event_bus/singleton.py"로 파일 경로를 직접 지정하고 있고 이 없이는
FastAPI 라우터·서비스·NotificationGateway가 서로 다른 EventBus 인스턴스를
갖게 되는 실제 버그가 생긴다 — 더 구체적인 05번 문서를 따른다.
"""
from __future__ import annotations

from src.core.event_bus.in_process import InProcessEventBus

_event_bus_instance: InProcessEventBus | None = None


def get_event_bus() -> InProcessEventBus:
    """FastAPI 앱 시작 시(§16.12 main.py의 lifespan) 1회 생성되고, 이후 모든
    라우터·서비스·NotificationGateway가 이 함수를 통해 동일 인스턴스를
    공유한다. FastAPI Depends로도 그대로 사용 가능:
    `event_bus: InProcessEventBus = Depends(get_event_bus)`."""
    global _event_bus_instance
    if _event_bus_instance is None:
        _event_bus_instance = InProcessEventBus()
    return _event_bus_instance


def reset_event_bus() -> None:
    """테스트 전용 — 모듈 레벨 싱글톤을 초기화한다."""
    global _event_bus_instance
    _event_bus_instance = None
