"""EO-05 `tests/adversarial/order_service/` 공용 픽스처.

`tests/integration/foundation/execution_ownership/conftest.py`의 `pool`을
그대로 재사용한다(`tests/adversarial/execution_ownership/conftest.py`와
동일 관례) — DSN 변환 로직을 두 곳에서 따로 관리하지 않는다."""
from __future__ import annotations

from tests.integration.foundation.execution_ownership.conftest import pool

__all__ = ["pool"]
