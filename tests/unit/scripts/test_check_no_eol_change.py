"""scripts/check_no_eol_change.py: block LF -> CRLF flips and CRLF new files, allow the rest."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_no_eol_change",
    Path(__file__).resolve().parents[3] / "scripts" / "check_no_eol_change.py",
)
assert spec is not None and spec.loader is not None
eol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eol)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def make_repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "lf.py").write_bytes(b"a = 1\nb = 2\n")
    (tmp_path / "crlf.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    (tmp_path / "note.txt").write_bytes(b"x\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "branch", "base")
    return tmp_path


def commit_all(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")


def test_flipping_an_lf_file_to_crlf_is_flagged(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "lf.py").write_bytes(b"a = 1\r\nb = 3\r\n")
    commit_all(repo)
    bad = eol.violations(eol.merge_base("base", repo), repo)
    assert len(bad) == 1 and "lf.py" in bad[0] and "LF -> CRLF" in bad[0]


def test_a_new_file_must_be_lf(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "new.py").write_bytes(b"x = 1\r\n")
    (repo / "fine.py").write_bytes(b"x = 1\n")
    commit_all(repo)
    bad = eol.violations(eol.merge_base("base", repo), repo)
    assert [b.split(":")[0] for b in bad] == ["new.py"]


def test_an_existing_crlf_file_may_stay_crlf_and_may_be_normalised(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "crlf.py").write_bytes(b"a = 1\r\nb = 9\r\n")  # 같은 스타일 유지
    (repo / "note.txt").write_bytes(b"y\r\n")  # 텍스트 확장자가 아니면 검사하지 않는다
    commit_all(repo)
    assert eol.violations(eol.merge_base("base", repo), repo) == []
    (repo / "crlf.py").write_bytes(b"a = 1\nb = 9\n")  # CRLF -> LF 정규화는 허용
    commit_all(repo)
    assert eol.violations(eol.merge_base("base", repo), repo) == []


def test_main_returns_nonzero_only_when_a_flip_exists(tmp_path: Path, capsys) -> None:
    repo = make_repo(tmp_path)
    assert eol.main(["--base", "base", "--repo", str(repo)]) == 0
    (repo / "lf.py").write_bytes(b"a = 1\r\n")
    commit_all(repo)
    assert eol.main(["--base", "base", "--repo", str(repo)]) == 1
    assert "EOL:" in capsys.readouterr().out
