"""Stub — original DEEPEN content moved to test_frozen.py / test_db_pool_retry.py.

The original 171-line __init__.py tests had review findings:
- test_allows_mutation_on_mock was tautological (MagicMock)
- test_succeeds_on_final_attempt had no failure injection
- No jitter sleep patching

Improved versions live in sibling test files.
"""

from __future__ import annotations
