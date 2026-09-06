"""L4-26 `tests/adversarial/oms/` 공용 픽스처.

`tests/integration/oms/conftest.py`의 `pool`을 그대로 재사용한다
(`tests/adversarial/market_data/conftest.py`와 동일한 re-export 패턴) —
새 커넥션 풀 로직을 추가하지 않는다.
"""
from __future__ import annotations

from tests.integration.oms.conftest import pool

__all__ = ["pool"]
