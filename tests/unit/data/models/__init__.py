"""DEEPEN(task-10007): negative/failure-injection coverage for src/data/models package.

DoD note: this file is intentionally minimal — all test functions live in
test_restored_deepen.py (7 tests). This __init__.py exists only to mark the
directory as a test package and to document the DoD path correction.

Scope: package-level invariants that span submodules — import structure,
enum validation, and failure-injection for submodule loading.
"""
