"""IND-2g -- generated TA-Lib catalog snapshot (aggregator).

DO NOT EDIT BY HAND. Regenerate with
`python -m src.core.indicators.catalog.generate_from_talib`.
Concatenates `talib_generated_partNN.py` (each kept under the architecture
guard's per-file line cap) into one `TALIB_GENERATED` tuple. Requires TA-Lib
installed to *regenerate* -- without it the generator raises
`TALibUnavailableError` instead of silently writing a partial/stale snapshot
(fail-closed). This file and its parts import nothing beyond the stdlib, so
they load without TA-Lib installed (CI-without-TA-Lib snapshot verification).
"""
from __future__ import annotations

from src.core.indicators.catalog.talib_generated_part00 import (
    TALIB_GENERATED_PART as _PART_00,
)
from src.core.indicators.catalog.talib_generated_part01 import (
    TALIB_GENERATED_PART as _PART_01,
)
from src.core.indicators.catalog.talib_generated_part02 import (
    TALIB_GENERATED_PART as _PART_02,
)
from src.core.indicators.catalog.talib_generated_part03 import (
    TALIB_GENERATED_PART as _PART_03,
)
from src.core.indicators.catalog.talib_generated_part04 import (
    TALIB_GENERATED_PART as _PART_04,
)
from src.core.indicators.catalog.talib_generated_part05 import (
    TALIB_GENERATED_PART as _PART_05,
)
from src.core.indicators.catalog.talib_generated_part06 import (
    TALIB_GENERATED_PART as _PART_06,
)
from src.core.indicators.catalog.talib_generated_part07 import (
    TALIB_GENERATED_PART as _PART_07,
)
from src.core.indicators.catalog.talib_generated_part08 import (
    TALIB_GENERATED_PART as _PART_08,
)
from src.core.indicators.catalog.talib_generated_part09 import (
    TALIB_GENERATED_PART as _PART_09,
)

TALIB_GENERATED: tuple[dict[str, object], ...] = (
    _PART_00 +
    _PART_01 +
    _PART_02 +
    _PART_03 +
    _PART_04 +
    _PART_05 +
    _PART_06 +
    _PART_07 +
    _PART_08 +
    _PART_09
)
