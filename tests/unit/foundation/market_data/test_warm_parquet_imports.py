"""DC-14 — import-surface gate for `warm_parquet.py`.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.2 DC-14.
DoD(e): 이 모듈이 실제로 import하는 parquet 관련 서드파티는 pyarrow뿐임을
소스 텍스트로 확인한다.

이 파일은 `warm_parquet.py`를 import하지 않는다 — pyarrow가 설치되지 않은
환경(예: 최소 컨테이너)에서도 게이트가 통과하도록 isolation 한다.
"""

from pathlib import Path

SRC = Path("src/foundation/market_data/adapters/storage/warm_parquet.py")


def test_only_pyarrow_import_is_used_for_parquet_io():
    """DoD(e): ArcticDB(BSL 1.1)·Timescale 컬럼스토어를 반입하지 않는다 —
    이 모듈이 실제로 import하는 parquet 관련 서드파티는 pyarrow뿐임을
    소스 텍스트로 확인한다."""
    src = SRC.read_text(encoding="utf-8")
    import_lines = [line for line in src.splitlines() if line.startswith(("import ", "from "))]
    assert any("pyarrow" in line for line in import_lines)
    assert not any("arcticdb" in line.lower() for line in import_lines)
    assert not any("timescale" in line.lower() for line in import_lines)
