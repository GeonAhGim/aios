# 05. 모듈 간 통신 아키텍처 (Draft) — v1.2

> **v1.2(2026-08-10) = "구현자 리뷰 대조" 라운드.** `InProcessEventBus` 글로벌
> 싱글톤 접근자(`get_event_bus()`) 신설 — 클래스만 있고 애플리케이션 전체가
> 공유할 방법이 없었음(각자 새 인스턴스를 만들면 서로 다른 큐를 가져
> publish/subscribe가 실제로는 연결 안 되는 상태였을 것).

> **v1.1(2026-08-10) = 번호 충돌 정정.** §5.1~5.6이 정책문서(docx) 5장
> (Agent 조직도, 5.1~5.7 — 특히 §5.3 "Topic 명명 규칙"은 이 프로젝트 전체가
> 가장 자주 인용해온 조항인데 정책문서 5.3 "Engineering Agent"와 번호가
> 겹치고 있었음)과 번호가 겹치는 것을 발견 — 01~11/13/15~17번과 동일한
> "0번부터 재검토" 패턴의 후속 발견(24개 최종 산출물 정리 단계에서 누락된
> 것을 사용자가 지적해 뒤늦게 완결). 모든 최상위 헤더를 "§5.X"로 전면 변경.

> 근거: AIOS 문서 4.1(Kernel Event Bus), 8.1(Trading Engine 파이프라인 개념도), 17.9-A(복잡성 억제 원칙)
> 결정: Phase 1은 **In-process Async Event Bus** (외부 메시지 브로커 없음)

## §5.1 왜 외부 브로커(Kafka/RabbitMQ/Redis Streams)를 쓰지 않는가

Phase 1 스콥(2026-08 확정): 심볼 5개 내외, 활성 거래소 1개(Bitget), KIS는 인터페이스만. 이 규모에서 외부 브로커는 인프라 복잡도만 늘리고 지연만 추가한다 — 17.9-A "설계 문서의 복잡도 ≠ 초기 구현 범위" 원칙의 실제 적용이다.

**단, Event Bus는 반드시 인터페이스 뒤에 숨긴다** — 나중에 규모가 커지면 내부 구현만 Redis Streams 등으로 교체하고, 이를 사용하는 코드(Scanner, 향후 Strategy 등)는 수정하지 않아도 되도록 설계한다.

## §5.2 Event Bus 인터페이스

```python
# src/core/event_bus/bus.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")
EventHandler = Callable[[T], Awaitable[None]]


class EventBus(ABC):
    """4.1 ⑫ Event Bus의 Trading Core 내부 구현.
    Phase 1: 단일 프로세스 내 asyncio 기반 in-memory 구현(InProcessEventBus)만 제공.
    향후 확장 시 이 인터페이스를 유지한 채 RedisEventBus 등으로 교체 가능."""

    @abstractmethod
    async def publish(self, topic: str, payload: T) -> None: ...

    @abstractmethod
    def subscribe(self, topic: str, handler: EventHandler) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown — 처리 중인 이벤트 완료 대기 후 종료."""
        ...


# src/core/event_bus/in_process.py
class InProcessEventBus(EventBus):
    """Phase 1 구현체. asyncio.Queue 기반 topic별 큐 + 워커 코루틴.
    단일 프로세스 내에서만 동작 — 다중 프로세스/서버 분산은 Phase 4+ 확장 대상."""

    def __init__(self):
        # STATUS: SCAFFOLD-READY
        raise NotImplementedError("Phase 1 SCAFFOLD 착수 대상 — 가장 먼저 구현 권장")
```

### 글로벌 싱글톤 접근자 (신규 — "구현자 리뷰 대조" 라운드에서 발견: `InProcessEventBus`
클래스는 정의됐으나, 애플리케이션 전체(FastAPI 라우터·서비스·notifications 등)가
**동일한 하나의 인스턴스**를 공유하는 방법이 어디에도 없었음 — 각자 새
인스턴스를 만들면 서로 다른 큐를 갖게 되어 publish/subscribe가 실제로는
연결되지 않는 상태가 될 뻔했다. `src/db/session.py`의 `get_db_session()`과
동일한 패턴으로 정정.)

```python
# src/core/event_bus/singleton.py
# STATUS: SCAFFOLD-READY
_event_bus_instance: "InProcessEventBus | None" = None

def get_event_bus() -> "InProcessEventBus":
    """FastAPI 앱 시작 시(§16.12 main.py의 lifespan) 1회 생성되고, 이후 모든
    라우터·서비스·NotificationGateway(16번 §16.7)가 이 함수를 통해 동일
    인스턴스를 공유한다. FastAPI Depends로도 그대로 사용 가능:
    `event_bus: InProcessEventBus = Depends(get_event_bus)`."""
    global _event_bus_instance
    if _event_bus_instance is None:
        _event_bus_instance = InProcessEventBus()
    return _event_bus_instance
```

- `src/api/main.py`(§16.12)의 FastAPI `lifespan` 컨텍스트에서 앱 시작 시
  `await get_event_bus().start()`, 종료 시 `await get_event_bus().stop()`을
  호출해야 한다(Graceful shutdown, §5.2 `stop()` 계약 준수) — 착수 시
  §16.12 코드에 반영 필요.

## §5.3 Topic 명명 규칙

```
{domain}.{entity}.{event_type}

예:
market.ticker.updated       — Ticker 갱신 (Parser → Scanner 등 구독)
market.orderbook.updated
market.distrust.entered     — 8.1-A Data Distrust Mode 진입 (전 모듈 구독 권장)
market.distrust.exited
order.status.changed        — Order 상태 전이 (8.3 State Machine과 연동)
reconciliation.discrepancy.detected  — 8.4
risk.circuit_breaker.level_changed   — 8.6
audit.decision.logged       — 8.10 (모든 주요 판단은 이 토픽으로도 발행)
```

Zone 경계 규칙: **FROZEN Zone 모듈(Strategy/Portfolio/Risk/Executor)이 구독하는 토픽은 지금 정의만 해두고, 실제 handler 등록은 15.6-D 이후로 미룬다.** SCAFFOLD 모듈(Loader/Parser/Validator/Scanner)은 지금 publish까지 구현한다.

## §5.4 동시성 모델 (Phase 1)

- 단일 Python 프로세스, `asyncio` 이벤트 루프 1개.
- 거래소 WebSocket 연결(Bitget)은 별도 코루틴으로 상시 실행, 수신 데이터를 Event Bus에 publish.
- 다중 프로세스/워커 분산은 Phase 1 스콥 밖 — 심볼 5개·거래소 1개 규모에서는 불필요.
- 이 결정은 8.2-D 지연 벤치마크(종단간 50ms) 설계와 직결된다 — 프로세스 간 통신 오버헤드가 없으므로 목표 달성이 상대적으로 쉽다.
- 큐 백프레셔 정책(최대 깊이, 초과 시 처리)은 08번 문서 §8.6 참조 — 테스트 계획과 함께 정의되어 있어 여기서는 중복 서술하지 않는다.

## §5.5 에러 처리 기본 원칙 (Phase 1)

```python
# src/core/event_bus/errors.py
class EventHandlerError(Exception):
    """핸들러 내부 예외를 감싼다. 원본 예외·topic·payload 요약을 포함해 audit_log에 기록."""


class HandlerCriticality(str, Enum):
    """v3.1 신설(09번 §9.1 #6) — 모든 handler를 동일하게 'log_and_continue'로 취급하면,
    상태 변경(포지션 갱신 등) handler의 실패까지 조용히 넘어가 내부 상태 불일치가
    실시간으로 드러나지 않는 위험이 있다. Handler 등록 시 이 값을 명시하도록 강제한다."""
    SAFE = "SAFE"          # 실패해도 시스템 상태에 영향 없음(예: 로깅용 구독자) — log_and_continue
    CRITICAL = "CRITICAL"  # 실패 시 상태 불일치 가능(예: 포지션/잔고 갱신) — 즉시 8.6 Circuit Breaker
                            # 경고 단계로 격상 + Human 알림, 재시도는 하되 무한 반복하지 않음(최대 5회)


class EventBusPolicy:
    ON_HANDLER_ERROR = {
        HandlerCriticality.SAFE: "log_and_continue",
        HandlerCriticality.CRITICAL: "escalate_and_retry",  # v3.1 변경 — 일괄 log_and_continue 폐기
    }
```

- 모든 handler 예외는 `audit_log`(8.10)에 자동 기록 — 별도 try/except를 각 handler마다 반복하지 않는다(Event Bus가 wrapping).
- **`CRITICAL` handler 등록 시 criticality를 명시하지 않으면 등록 자체를 거부한다** — 기본값을 두지 않아, 개발자가 실수로 상태 변경 로직을 SAFE로 잘못 분류하지 못하게 한다.
- 거래소 API 호출 실패(WebSocket 끊김 등)는 `market.distrust.entered`류 이벤트로 전환해 다른 모듈에 알린다 — 조용히 재시도만 하고 넘어가지 않는다.
- 재시도 정책(Phase 1 Draft): 지수 백오프, 최대 5회, 초기 지연 1초 — Bitget Rate Limit 고려해 조정 필요(실제 문서 확인 후 확정).

## §5.6 In-memory Bus ↔ 영속 DB 정합성 원칙 (v3.1 신설, 09번 §9.1 #7)

Phase 1의 `InProcessEventBus`는 프로세스 메모리에만 존재한다 — 프로세스가 재시작되면 큐에 남아있던 미처리 이벤트는 소실된다. 그런데 DB(PostgreSQL)는 영속적이므로, "DB에는 기록됐지만 그 사실을 알리는 이벤트는 발행되지 못한" 상태가 재시작 시점에 발생할 수 있다.

**원칙: DB 쓰기가 이벤트 발행보다 먼저(또는 같은 트랜잭션 경계 안에서) 일어난다.** 즉 이벤트는 "이미 벌어진 사실의 알림"이지 "사실 자체"가 아니다 — 이벤트가 유실되어도 진실은 항상 DB에 있다.

- 프로세스 시작 시 복구 절차(Draft): `orders` 테이블에서 `status`가 아직 최종 상태(FILLED/CANCELLED/REJECTED/EXPIRED/FAILED)가 아닌 행을 조회 → 각 행에 대해 `ExchangeAdapter.get_order()`로 실제 상태 재확인(7.5 UNKNOWN 처리 원칙과 동일 로직 재사용) → 필요한 후속 이벤트를 재발행.
- 이 복구 절차 자체도 `audit_log`에 기록해, "재시작 후 몇 건을 재동기화했는지"가 항상 추적 가능하게 한다.
- Phase 4+에서 영속적 메시지 브로커(Redis Streams 등)로 전환하면 이 복구 절차의 필요성은 줄어들지만, DB가 항상 진실의 원천이라는 원칙 자체는 유지한다.
