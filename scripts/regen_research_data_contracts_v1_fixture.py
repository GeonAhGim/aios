"""Regenerate tests/foundation/unit/research_data/fixtures/research_data_contracts_v1.json
from the live `src.foundation.research_data.contracts.v1` models.

Run this whenever a model docstring (schema `description`) changes and
`test_schema_snapshot_matches_fixture` starts failing on description-only
diffs. It must NOT be used to silently absorb a field/type/required/enum
change -- diff the script's output against the old fixture first (e.g. via
`git diff`) and confirm only descriptions moved before committing.

Usage: python scripts/regen_research_data_contracts_v1_fixture.py
"""
from __future__ import annotations

import json
from pathlib import Path

from src.foundation.research_data.contracts import v1

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "foundation"
    / "unit"
    / "research_data"
    / "fixtures"
    / "research_data_contracts_v1.json"
)

_MODELS = (v1.ResearchItem, v1.SourceMeta)


def main() -> None:
    schema = {m.__name__: m.model_json_schema() for m in _MODELS}
    FIXTURE.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {FIXTURE}")


if __name__ == "__main__":
    main()
