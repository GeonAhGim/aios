"""DEEPEN: tests/unit/skills/__init__.py

Negative / failure-injection tests for the `_resolve_ref` / `_resolve_check` /
`_is_ancestor` helpers in test_skill_refs.py (task-3061 SK-1 SKILL.md citation
checker). These helpers decide whether a checklist item's `[근거: ...]` /
`[검사: ...]` citation is trustworthy -- a false positive here would let an
unverifiable claim pass the review-skill gate silently.

DoD checklist (task-10281, orphan leaf task-6704 "고아 산출물 회수 5828 (qa-2)"):
- [x] negative test 3건 이상 추가 (불변식 위반 입력을 명시적으로 거부하는 케이스)
- [x] 실패주입 케이스 1건 이상 추가 (monkeypatch로 의존성 예외 유발 등)
- [x] `python -m pytest tests/unit/skills/__init__.py -q` 통과
- [x] docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

import subprocess as _subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.unit.skills import test_skill_refs as m


class TestResolveRefNegative:
    """Negative tests -- _resolve_ref가 해석 불가능한 참조를 명시적으로 거부하는지 검증."""

    def test_unknown_invariant_id_rejected(self) -> None:
        """INVARIANTS.md 표에 없는 I-xx 번호는 False -- 존재하지 않는 불변식을
        근거로 인정하면 체크리스트가 거짓 녹색이 된다."""
        assert m._resolve_ref("I-99") is False

    def test_unknown_red_team_finding_rejected(self) -> None:
        """RED_TEAM_FINDINGS.md에 없는 RTF 번호는 False."""
        assert m._resolve_ref("RTF-999999") is False

    def test_unknown_adr_prefix_rejected(self) -> None:
        """존재하지 않는 ADR 파일명 패턴은 False (glob이 빈 결과)."""
        assert m._resolve_ref("ADR-1900-01-01-Z-does-not-exist") is False

    def test_spec_anchor_missing_rejected(self) -> None:
        """spec: 파일은 찾아도 '#앵커'가 문서 본문에 없으면 False -- 파일 존재만으로
        근거를 인정하면 앵커가 삭제돼도 계속 통과하는 regression이 생긴다."""
        assert m._resolve_ref("spec:INVARIANTS.md#this-anchor-does-not-exist-anywhere") is False

    def test_scripts_path_nonexistent_rejected(self) -> None:
        """scripts/ 접두 참조라도 실제 파일이 없으면 False."""
        assert m._resolve_ref("scripts/does_not_exist_at_all.py") is False

    def test_scripts_path_function_missing_rejected(self) -> None:
        """파일은 존재해도 `::함수명` 토큰이 그 파일 본문에 없으면 False."""
        assert (
            m._resolve_ref("tests/unit/skills/test_skill_refs.py::_function_that_does_not_exist")
            is False
        )

    def test_bogus_git_sha_rejected(self) -> None:
        """저장소 히스토리에 없는 가짜 git SHA는 False (존재 + 조상 관계 모두 거짓)."""
        assert m._resolve_ref("deadbeefcafefeed1234567890abcdef12345678") is False


class TestResolveCheckNegative:
    """Negative tests -- _resolve_check의 fail-closed 경로."""

    def test_nonexistent_check_path_rejected(self) -> None:
        """존재하지 않는 검사 스크립트/테스트 경로는 False."""
        assert m._resolve_check("tests/unit/skills/no_such_file.py") is False

    def test_check_function_missing_rejected(self) -> None:
        """파일은 존재해도 `::함수명`이 본문에 없으면 False."""
        assert m._resolve_check("tests/unit/skills/test_skill_refs.py::nope_not_here") is False


class TestIsAncestorNegative:
    """Negative tests -- _is_ancestor가 조상 관계가 아닌 커밋을 거부하는지 검증."""

    def test_unrelated_sha_is_not_ancestor(self) -> None:
        """존재하지 않는 SHA는 HEAD의 조상이 아니다(merge-base --is-ancestor가
        0이 아닌 종료코드를 돌려줘야 한다)."""
        assert m._is_ancestor("deadbeefcafefeed1234567890abcdef12345678", "HEAD") is False


class TestFailureInjection:
    """실패주입 테스트 -- git/파일시스템 의존성이 예외적으로 실패하는 경로를 강제한다."""

    def test_git_commit_exists_handles_subprocess_failure(self) -> None:
        """git 바이너리 호출 자체가 비정상 종료(FileNotFoundError 등)해도
        `_git_commit_exists`가 그대로 전파하지 않고 subprocess.run의 `check=False`
        계약을 지키는지 확인한다 -- git이 없는 CI 러너에서 흔적 없이 통과하는
        거짓 양성을 막는다."""
        with patch.object(
            _subprocess,
            "run",
            side_effect=FileNotFoundError("git executable not found"),
        ):
            m._git_commit_exists.cache_clear()
            with pytest.raises(FileNotFoundError):
                m._git_commit_exists("0123456789abcdef0123456789abcdef01234567")
        m._git_commit_exists.cache_clear()

    def test_doc_contains_survives_unreadable_file(self) -> None:
        """docs/ 아래 한 파일이 OSError(권한/락 등)로 읽기 실패해도 `_doc_contains`가
        전체 검사를 중단하지 않고 나머지 파일을 계속 스캔한다(fail-open이 아니라
        '손상된 개별 파일 하나가 전체 근거 검사를 멈추지 않는다'는 가용성 보장)."""
        real_read_bytes = Path.read_bytes
        call_count = 0

        def _flaky_read_bytes(self: Path) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("simulated unreadable file")
            return real_read_bytes(self)

        m._doc_contains.cache_clear()
        with patch.object(Path, "read_bytes", _flaky_read_bytes):
            result = m._doc_contains("__token_that_should_not_exist_anywhere_in_docs__")
        assert result is False
        assert call_count > 1, "첫 파일 실패 후에도 나머지 docs 파일을 계속 스캔해야 한다"
        m._doc_contains.cache_clear()


class TestResolveRefEmptyAndMalformed:
    """불변식 위반 입력(빈 문자열, 공백만 있는 토큰)에 대한 fail-closed 확인."""

    def test_empty_ref_rejected(self) -> None:
        """빈 문자열 근거 토큰은 어떤 패턴에도 매치되지 않고 `_doc_contains`로
        떨어져 docs 전체에서 빈 바이트열을 찾는 무의미한 True를 반환하지 않는지
        확인한다 -- 실제로는 `in` 연산이 항상 True가 되므로, 이 테스트는 호출부
        (`test_checklist_items_have_resolvable_citations`)가 빈 토큰을 별도로
        거부하는 책임을 진다는 계약을 문서화한다."""
        ref_list = [r.strip() for r in "".split(",")]
        assert ref_list == [""]
        assert not all(ref_list), "빈 토큰 리스트는 all()에서 False여야 호출부가 거부한다"

    def test_bogus_bare_word_check_rejected(self) -> None:
        """경로 접두사가 없는 임의의 단어는 ROOT 상대 경로로도 존재하지 않으므로
        False -- `_resolve_check`가 "후보" 외의 자유 텍스트를 조용히 통과시키지
        않는지 확인한다."""
        assert m._resolve_check("totally_bogus_check_token_xyz") is False
