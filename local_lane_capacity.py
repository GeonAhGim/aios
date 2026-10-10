"""Removed (task-11585): this was fleet CI-orchestrator code (healthcheck/orchestrator/
task_store/local_triage), accidentally committed to the backend repo by f53303eac instead
of C:\\aios\\pm. Nothing under src/ or scripts/ references it. Content emptied rather than
`git rm` because the pre-push gate's ruff step lints the diff's changed paths including
deleted ones and errors E902 on a path that no longer exists -- a gate defect unrelated to
this fix (workers cannot edit C:\\aios\\pm). File removal should happen once that gate defect
is fixed.
"""
