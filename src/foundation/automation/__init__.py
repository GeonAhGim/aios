"""U-4a no-code automation rule engine (ADR-2026-09-09-B Decision C, U-4).

This leaf covers domain/application core only: rule schema (`contracts/v1.py`),
pure condition evaluation (`domain/evaluate.py`), and deterministic historical
preview (`application/preview_rule.py`). The HTTP router
(`src/api/routers/rules.py`) and the Telegram notifier adapter are split into a
follow-up leaf (task-2631 decision, after 3 prior `error_max_turns` attempts
that tried to build router+adapter+domain in one leaf).
"""
