"""Removed (task-11585): this collected test imported fleet CI-orchestrator modules
(healthcheck/orchestrator) that only exist in C:\\aios\\pm, not this repo, causing
`ModuleNotFoundError: No module named 'healthcheck'` during pytest collection everywhere
this file is checked out (e.g. the ci worktree). Content emptied rather than `git rm`
because the pre-push gate's ruff step errors E902 on deleted paths -- a gate defect
unrelated to this fix (workers cannot edit C:\\aios\\pm). File removal should happen once
that gate defect is fixed.
"""
