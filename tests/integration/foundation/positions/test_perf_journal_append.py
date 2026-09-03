"""LB-18 계약·성능 — 저널 append(락 포함) p95 < 30ms(100회, 실 DB).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§7 B("저널 append(락
포함), 측정 지점 record_fill, p95 < 30ms"), §8.4("저널 append p95 < 30ms(100회)").

측정 대상은 `record_fill()` 전체(락 획득 → 조회 → 원가법 계산 → 저널 append
→ 스냅샷 upsert → 감사) 호출 1회다 — §7 표가 측정 지점을 `record_fill`로
못박아 두므로 `PostgresJournalRepository.append()`만 떼어 재지 않는다(그건
`pos_journal_append_ms` 메트릭 이름과는 별개로 §7 B행이 명시한 측정 지점이
아니다). `tests/integration/foundation/ledger/test_perf_journal.py`(LC-17)와
같은 이유로 `pytest-benchmark` 대신 수동 p95를 쓴다 — 매 회 실제 사용 패턴
(짧은 트랜잭션 하나, 새 커넥션 획득·해제 포함)을 그대로 재현한다.

매 회 새 position_key(신규 tenant/account)로 첫 체결을 기록한다 — 같은
position_key를 재사용하면 `_acquire_position_lock`의 advisory lock이 반복마다
경쟁하지 않아 측정이 더 낙관적으로 나올 뿐 아니라, `test_race_fills.py`가 이미
같은 position_key 동시성은 별도로 검증한다(이 테스트의 관심사가 아니다).

`@pytest.mark.perf`: `test_race_fills.py`(20-way 동시 커넥션)와 같은 pytest
실행에 섞이면 공유 `TEST_DATABASE_URL` 인스턴스의 IO 경합으로 p95가 튈 수
있다(task-489 note) — 이 마커로 동시성 부하 테스트와 분리 실행한다(공유
Postgres 경합 자체의 해소는 PLT-36/task-460의 xdist worker별 DB 격리 범위,
이 테스트 전용 리프에서 임계값을 낮추거나 DB 환경을 바꾸지 않는다).

task-653(LB-18 후속)에서 `record_fill()` 호출당 순차 DB 왕복을 ~11회에서
~9회로 줄였다: 멱등 사전조회를 `journal.list_for`(O(n))에서 `idempotency_key`
UNIQUE 인덱스 EXISTS(O(1))로 바꾸고, `journal.append` 내부의 멱등 조회·
snapshot 소유자 조회·last-entry 조회 3왕복을 LEFT JOIN 통합 SELECT 1회로
합쳤다(advisory lock은 단독 왕복으로 유지 — PG가 FROM절을 lock 함수보다
먼저 평가해 같은 SELECT에 lock을 얹으면 20-way 동시 append가 실제로
깨진다, `postgres_journal_repository.py` 주석 참고). 정합성(락 범위·
sequence_no 연속성·감사 1:1)은 약화하지 않았다 — `test_postgres_journal_repository.py`
전체와 20-way 동시성 테스트가 그대로 통과함으로 확인했다. 아래 단언은
스펙이 요구하는 목표(30ms)를 그대로 건다 — 로컬 공유 머신의 동시 부하로
인한 실패는 이 단언을 약화(xfail/marker 분리/임계 완화)할 사유가 아니다
(§8.4 계약, task-653 decision). 최종 판정 환경은 이 파일이 아니라
local_ci(전용 worktree, 직렬 실행)다."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.contracts.v1 import RecordFillCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

_SAMPLE_COUNT = 100
_TARGET_P95_MS = 30.0
_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _key() -> str:
    return str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="perf",
        )
    )


@pytest.mark.perf
async def test_record_fill_journal_append_p95_under_30ms(pool) -> None:
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)

    commands: list[RecordFillCommand] = []
    for _ in range(_SAMPLE_COUNT):
        tenant_id = await create_test_user(pool)
        account_id = await create_pos_account(pool, tenant_id)
        position_key = _key()
        await open_position(
            pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
        )
        commands.append(
            RecordFillCommand(
                tenant_id=tenant_id,
                account_id=account_id,
                position_key=position_key,
                order_id=uuid4(),
                fill_seq=1,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                price=Money(amount=Decimal("100"), currency=Currency.KRW),
                fee=None,
                occurred_at=_OCCURRED_AT,
                trace_id=uuid4(),
            )
        )

    latencies_ms: list[float] = []
    for command in commands:
        started = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            await record_fill(
                conn,
                command,
                asset_class=AssetClass.CRYPTO,
                journal=journal,
                snapshots=snapshots,
                audit=audit,
                clock=_clock,
            )
        latencies_ms.append((time.perf_counter() - started) * 1000)

    latencies_ms.sort()
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]

    print(
        f"\npositions record_fill journal append latency: "
        f"p95={p95_ms:.3f}ms (n={len(latencies_ms)})"
    )

    assert len(latencies_ms) == _SAMPLE_COUNT
    assert p95_ms < _TARGET_P95_MS, (
        f"record_fill 저널 append p95({p95_ms:.3f}ms)가 목표({_TARGET_P95_MS}ms)를 초과했습니다."
    )
