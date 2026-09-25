"""tests/perf/oms/ 공용 픽스처 — task-2804(DEEPEN 2329) L4-28 보강판.

이 디렉터리는 `tests/performance/oms/`(원 리프 task-2323, commit `034fed00`)와
같은 4개 측정 대상을 다시 재는 대신, DEPTH 감사(task-2722,
`docs/audit/DEPTH_L4_BR.md` #2329행)가 지적한 두 가지 결손만 보강한다:
(1) 실패 주입 테스트 부재, (2) 절대 지연/처리량이 게이트가 아니라 print라
수치 단언 기준이 약하다는 점. 스키마 시딩·DB 왕복 로거는 원 리프가 이미
검증한 `tests/performance/oms/conftest.py`/`_fixtures.py`를 그대로 재사용한다
(중복 정의 없음) — `pool`/`_drain_shared_queues` 픽스처를 여기 다시 임포트해
`tests/perf/oms/` 하위 테스트에서도 쓸 수 있게 한다(pytest 픽스처 재노출은
공식 지원 패턴, https://docs.pytest.org/en/stable/how-to/fixtures.html).
"""
from __future__ import annotations

from tests.performance.oms.conftest import (
    _drain_shared_queues,  # noqa: F401
    attach_round_trip_logger,  # noqa: F401
    measure_baseline_round_trip_p95_ms,  # noqa: F401
    pool,  # noqa: F401
)
