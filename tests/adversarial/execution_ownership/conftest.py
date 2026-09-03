"""EO-04 `tests/adversarial/execution_ownership/` 공용 픽스처.

`tests/integration/foundation/execution_ownership/conftest.py`의 `pool`을
그대로 재사용한다(ledger 적대적 테스트 디렉터리의 conftest.py와 동일
관례) — 이 디렉터리가 별도 사본을 만들면 DSN 변환 로직이 두 곳에서
따로 관리된다. 새 로직을 추가하지 않고 import re-export만 한다.
"""
from __future__ import annotations

from tests.integration.foundation.execution_ownership.conftest import pool

__all__ = ["pool"]
