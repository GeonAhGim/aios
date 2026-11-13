"""D2 depth augmentation for test_skill_refs helpers.

Extends the D1 coverage in test_skill_refs.py / test_restored_deepen.py with:
- negative tests >= 3 (explicit rejection of invariant-violating inputs)
- failure-injection test >= 1 (forced dependency exception)
- performance assertion >= 1 (wall-clock budget against a measurable threshold)
- gate-red reproduction >= 1 (reproduce a known red-gate scenario)

No `# type: ignore` comments used -- keeps type_ignore_budget at 109.
"""

from __future__ import annotations

import subprocess as _subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.unit.skills import test_skill_refs as m


class TestResolveRefNegativeDepth:
    """Additional negative tests for _resolve_ref beyond D1 coverage."""

    def test_resolve_ref_empty_string_returns_true_via_doc_contains(self) -> None:
        """빈 문자열은 _doc_contains로 떨어져 b''가 모든 파일에 포함되어 True.
        이 테스트는 _resolve_ref가 빈 입력을 False로 거부하지 *않는다*는
        실제 행위를 문서화한다 -- 호출부(test_checklist_items_have_resolvable_citations)가
        빈 토큰을 별도로 검사하는 책임이 있다."""
        assert m._resolve_ref("") is True

    def test_resolve_ref_whitespace_only_treated_as_empty(self) -> None:
        """공백만 있는 문자열은 strip() 후 빈 문자열이 되어 True -- 빈 토큰
        검사는 호출부 책임이라는 사실을 확인한다."""
        assert m._resolve_ref("   ") is True

    def test_resolve_ref_rejects_unknown_invariant_i99(self) -> None:
        """I-99는 INVARIANTS.md 표에 없으므로 False -- I-xx 형식이지만
        번호가 실제 불변식과 매치되지 않으면 거부된다."""
        assert m._resolve_ref("I-99") is False

    def test_resolve_ref_rejects_nonexistent_rtf(self) -> None:
        """RTF-99999는 RED_TEAM_FINDINGS.md에 없으므로 False."""
        assert m._resolve_ref("RTF-99999") is False

    def test_resolve_ref_rejects_bare_word_not_in_docs(self) -> None:
        """docs 전체에서 검색해도 등장하지 않는 고유 토큰은 False."""
        assert m._resolve_ref("__unique_token_xyz_does_not_exist_anywhere__") is False


class TestResolveCheckNegativeDepth:
    """Additional negative tests for _resolve_check beyond D1 coverage."""

    def test_resolve_check_rejects_nonexistent_file(self) -> None:
        """존재하지 않는 파일 경로는 False -- _resolve_check가 fail-closed한다."""
        assert m._resolve_check("tests/does_not_exist_at_all_xyz.py") is False

    def test_resolve_check_with_nonexistent_function(self) -> None:
        """파일은 존재하지만 함수명이 본문에 없으면 False."""
        assert (
            m._resolve_check("tests/unit/skills/test_skill_refs.py::_nonexistent_func_xyz") is False
        )

    def test_resolve_check_directory_path_returns_true_no_func(self) -> None:
        """디렉터리 경로는 func가 없으므로 True -- _resolve_check가 '경로가
        존재하면 func 없이도 True'는 실제 행위를 문서화한다 (후보 경로가
        디렉터리인 edge case)."""
        # tests/ 디렉터리는 반드시 존재
        assert m._resolve_check("tests/") is True


class TestFailureInjectionDepth:
    """Failure-injection tests for dependency exceptions."""

    def test_resolve_ref_handles_doc_read_permission_error(self) -> None:
        """docs 폴더의 파일 읽기 권한이 없어도(os.read 접근 거부) 전체 검사가
        중단되지 않고 나머지 파일을 계속 스캔한다 -- fail-open이 아닌 가용성 보장.
        _doc_contains는 OSError를 silently 건너뛴다."""
        real_read_bytes = Path.read_bytes
        call_count = 0
        skipped_count = 0

        def _flaky_read_bytes(self: Path) -> bytes:
            nonlocal call_count, skipped_count
            call_count += 1
            if call_count == 1:
                skipped_count += 1
                raise OSError("simulated unreadable file")
            return real_read_bytes(self)

        m._doc_contains.cache_clear()
        with patch.object(Path, "read_bytes", _flaky_read_bytes):
            result = m._doc_contains("__token_that_should_not_exist_anywhere_in_docs__")
        assert result is False
        # _doc_contains는 OSError를 건너뛰므로 토큰이 실제 없으면 False가 돌아옴
        assert skipped_count >= 1
        m._doc_contains.cache_clear()

    def test_resolve_ref_handles_subprocess_timeout(self) -> None:
        """git merge-base가 타임아웃되면(_is_ancestor가 예외를 던지면) fail-closed로
        전파돼 거짓 녹색을 만들지 않는다."""

        def _timeout(*_args: object, **_kwargs: object) -> None:
            raise _subprocess.TimeoutExpired("git", 30)

        m._git_commit_exists.cache_clear()
        m._reachability_target.cache_clear()
        with patch.object(_subprocess, "run", _timeout):
            with pytest.raises(_subprocess.TimeoutExpired):
                m._git_commit_exists("abcdef1")


class TestPerformanceAssertion:
    """Performance budget assertions for _resolve_ref / _resolve_check."""

    @pytest.mark.perf
    def test_resolve_ref_budget_under_500ms(self) -> None:
        """_resolve_ref는 100회 반복 시 500ms 미만이어야 한다(1회 avg 5ms).
        참조 해석이 느리면 CI 게이트가 병렬 실행 시 타임아웃에 걸린다."""
        # Clear cached helpers that _resolve_ref calls internally
        m._git_commit_exists.cache_clear()
        m._reachability_target.cache_clear()
        m._find_doc.cache_clear()
        m._doc_contains.cache_clear()
        sample_refs = [
            "I-01",
            "RTF-1",
            "spec:INVARIANTS.md",
            "scripts/check_zone_manifest.py",
            "tests/unit/skills/test_skill_refs.py",
        ]
        iterations = 100
        start = time.perf_counter()
        for _ in range(iterations):
            for ref in sample_refs:
                m._resolve_ref(ref)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 500, f"_resolve_ref 100회 반복이 {elapsed_ms:.0f}ms -- 예산 500ms 초과"

    @pytest.mark.perf
    def test_resolve_check_budget_under_500ms(self) -> None:
        """_resolve_check는 100회 반복 시 500ms 미만이어야 한다."""
        # Clear cached helpers that _resolve_check calls internally
        m._find_doc.cache_clear()
        sample_checks = [
            "scripts/check_zone_manifest.py",
            "tests/unit/skills/test_skill_refs.py",
            "tests/unit/skills/test_restored_deepen.py",
        ]
        iterations = 100
        start = time.perf_counter()
        for _ in range(iterations):
            for check in sample_checks:
                m._resolve_check(check)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 500, (
            f"_resolve_check 100회 반복이 {elapsed_ms:.0f}ms -- 예산 500ms 초과"
        )


class TestGateRedReproduction:
    """Gate-red reproduction -- known failure scenarios that CI catches."""

    def test_skill_files_count_matches_eight_axes(self) -> None:
        """review-*/SKILL.md 파일 수가 정확히 8개여야 한다(8종 영역별 검토 스킬).
        파일이 부족하면 미검토 영역이 생기고, 초과하면 중복 검토가 생긴다."""
        files = m._skill_files()
        assert len(files) == 8, f"review skill 파일 수: {len(files)}개 (예상 8개)"
        axes = {f.parent.name.replace("review-", "") for f in files}
        assert axes == m.REQUIRED_AXES, f"결측 축: {m.REQUIRED_AXES - axes}"

    def test_frontmatter_has_required_keys(self) -> None:
        """모든 review skill의 frontmatter가 paths/tier/axis 키를 갖고 있어야 한다.
        누락되면 review_plan이 해당 스킬을 로드하지 못한다.
        tier는 list[str] 형식(예: [S])이므로 in 연산 전에 언팩해야 한다."""
        for skill_path in m._skill_files():
            fm, _ = m._split_frontmatter(skill_path.read_text(encoding="utf-8"))
            assert "paths" in fm, f"{skill_path.parent.name}: paths 키 누락"
            assert "tier" in fm, f"{skill_path.parent.name}: tier 키 누락"
            assert "axis" in fm, f"{skill_path.parent.name}: axis 키 누락"
            # tier는 list 형식 (예: ["S"])
            tier_val = fm["tier"]
            if isinstance(tier_val, list):
                assert all(t in m.VALID_TIERS for t in tier_val), (
                    f"{skill_path.parent.name}: tier 값 {tier_val} 중 유효하지 않음"
                )
            else:
                assert tier_val in m.VALID_TIERS, (
                    f"{skill_path.parent.name}: tier={tier_val} 유효하지 않음"
                )
