"""PLT-43 정적 검사 — actor≠counterparty 비교를 이 모듈 밖에서 재발명하지
않는지 단언한다.

ADR-2026-09-06-G §9: "'작성자≠승인자'가 기능마다 따로 구현된다" 문제를
막기 위해 `segregation_of_duty.assert_actor_not_counterparty()`를 유일한
정의로 둔다. 이 테스트는 세 가지를 확인한다.

1. `src/` 전체에 이 모듈 밖에서 actor/approver 계열 식별자와 counterparty/
   requester/proposer 계열 식별자를 직접 `==`/`!=`로 비교하는 코드가 없다
   (있으면 인라인 재발명 — 이 모듈을 거치도록 고쳐야 한다).
2. 실존하는 세 호출처(`core/approval/service.py`의 DUAL 2차 서명 검사,
   `mandates/application/activate_revision.py`의 CM-5 작성자≠승인자,
   `api/routers/admin_break_glass.py`의 PLT-35 자기승인 방지 — RATCHET-2
   core-no-io로 인해 task-5311부터 `core/security/break_glass.py`가 아니라
   이 composition root가 import한다)가 모두 이 모듈을 실제로 import한다.
3. 게이트 적색 재현(DEEPEN task-3192): `core/security/break_glass.py`는
   2026-09-16 이전에는 이 primitive를 거치지 않고
   `existing["requester_id"] == approver_id`처럼 dict-subscript로 감싼
   피연산자·역순 비교로 자기승인을 직접 재발명했다 — 원래의
   `_REINVENTION_PATTERN`(단순 `\\w` 경계, ACTOR==COUNTERPARTY 단일 순서만
   가정)은 이 형태를 잡아내지 못해 실제로 배선 누락이 리뷰를 통과했다.
   `test_gate_red_reinvention_with_subscript_and_reversed_order_is_detected`는
   그 정확한 문구를 합성 소스 트리에 재현해, 현재 패턴이 이를 탐지함을
   증명한다 — 이 강화가 되돌려지면(원래의 좁은 패턴으로 회귀하면) 이
   테스트가 즉시 적색이 된다.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[4] / "src"

_EXEMPT_SUFFIXES = ("segregation_of_duty.py",)

# actor측: 이 action을 수행하려는 주체. counterparty측: 이미 그 action에
# 관여한(또는 그 action의 대상인) 다른 주체. 두 측을 직접 == / != 로
# 비교하는 건 segregation_of_duty 모듈이 독점해야 하는 판단이다.
#
# 각 피연산자는 `existing["requester_id"]`나 `row.approver_id`처럼
# dict-subscript/attribute 접근으로 감싸일 수 있으므로, 키워드 앞뒤로 그
# 형태(`foo[`/`."`/닫는 `]`/따옴표)를 허용한다. 비교 순서도
# ACTOR==COUNTERPARTY와 COUNTERPARTY==ACTOR 양쪽 다 잡는다(`==`는
# 대칭이라 실제 코드가 어느 순서로 쓸지 보장되지 않는다).
_ACTOR_KEYWORDS = "actor|approver|admin_id|signer"
_COUNTERPARTY_KEYWORDS = "counterpart|requester|proposer|requested_by|proposed_by|approver"


def _operand(keywords: str) -> str:
    return rf"(?:\w+\s*\[\s*['\"]?)?\w*(?:{keywords})\w*(?:['\"]?\s*\])?"


_ACTOR_SIDE = _operand(_ACTOR_KEYWORDS)
_COUNTERPARTY_SIDE = _operand(_COUNTERPARTY_KEYWORDS)
_REINVENTION_PATTERN = re.compile(
    rf"\b{_ACTOR_SIDE}\s*(?:==|!=)\s*{_COUNTERPARTY_SIDE}\b"
    rf"|\b{_COUNTERPARTY_SIDE}\s*(?:==|!=)\s*{_ACTOR_SIDE}\b"
)

_KNOWN_CALL_SITES = (
    "core/approval/service.py",
    "foundation/mandates/application/activate_revision.py",
    "api/routers/admin_break_glass.py",
)


def _iter_py_files(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if not p.name.endswith(_EXEMPT_SUFFIXES) and "__pycache__" not in p.parts
    ]


def _blank_strings_and_comments(source: str) -> list[str]:
    """주석과 여러 줄 문자열(모듈/함수 docstring)만 공백으로 지운다 —
    "requester != approver" 같은 설명 문구가 실제 코드 비교로 오탐되지
    않게 하기 위해서다. 한 줄짜리 문자열은 그대로 둔다: `existing
    ["requester_id"] == approver_id`처럼 dict-subscript 키 문자열 자체가
    재발명 탐지 대상이므로, 이것까지 지우면 탐지 대상이 사라진다."""
    lines = [list(line) for line in source.splitlines(keepends=False)]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                pass
            elif tok.type == tokenize.STRING and tok.start[0] != tok.end[0]:
                pass  # 여러 줄에 걸친 문자열(docstring)만 대상
            else:
                continue
            (start_row, start_col), (end_row, end_col) = tok.start, tok.end
            for row in range(start_row, end_row + 1):
                if row - 1 >= len(lines):
                    continue
                line = lines[row - 1]
                col_from = start_col if row == start_row else 0
                col_to = end_col if row == end_row else len(line)
                for i in range(col_from, min(col_to, len(line))):
                    line[i] = " "
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass  # tokenize 실패(합성 테스트 스니펫 등) 시 원문 그대로 스캔한다
    return ["".join(line) for line in lines]


def _find_offenders(root: Path) -> list[str]:
    offenders: list[str] = []
    for path in _iter_py_files(root):
        text = path.read_text(encoding="utf-8")
        code_lines = _blank_strings_and_comments(text)
        original_lines = text.splitlines()
        for lineno, code_line in enumerate(code_lines, start=1):
            if _REINVENTION_PATTERN.search(code_line):
                offenders.append(
                    f"{path.relative_to(root)}:{lineno}: {original_lines[lineno - 1].strip()}"
                )
    return offenders


def test_no_inline_reinvention_outside_segregation_of_duty_module() -> None:
    offenders = _find_offenders(SRC_ROOT)
    assert not offenders, (
        "actor != counterparty 비교는 "
        "src/foundation/trust/domain/rules/segregation_of_duty.py의 "
        "assert_actor_not_counterparty()를 거쳐야 합니다. 재발명 발견:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("relative_path", _KNOWN_CALL_SITES)
def test_known_call_site_imports_segregation_of_duty(relative_path: str) -> None:
    text = (SRC_ROOT / relative_path).read_text(encoding="utf-8")
    assert "trust.domain.rules.segregation_of_duty import" in text
    assert "assert_actor_not_counterparty" in text


# ---- 게이트 적색 재현 --------------------------------------------------------


def test_gate_red_reinvention_with_subscript_and_reversed_order_is_detected(
    tmp_path: Path,
) -> None:
    """`core/security/break_glass.py`가 실제로 배포됐던 재발명 문구(dict
    subscript로 감싼 피연산자 + counterparty==actor 역순)를 합성 소스
    트리에 그대로 재현한다. 이 정확한 한 줄 때문에 배선 누락이 리뷰를
    통과했었다 — 이 테스트는 현재 스캐너가 그 문구를 잡아냄을 실행으로
    증명하고, 향후 패턴이 원래의(더 좁은) 형태로 되돌아가면 즉시 적색이
    된다."""
    offending_file = tmp_path / "break_glass.py"
    offending_file.write_text(
        "async def approve_grant(existing, approver_id):\n"
        '    if existing["requester_id"] == approver_id:\n'
        "        raise SelfApprovalError()\n",
        encoding="utf-8",
    )

    offenders = _find_offenders(tmp_path)

    assert len(offenders) == 1
    assert "requester_id" in offenders[0]
    assert "approver_id" in offenders[0]


def test_gate_red_reversed_keyword_order_alone_is_detected(tmp_path: Path) -> None:
    """subscript 없이 순서만 뒤집힌 `requester_id == approver_id` 형태도
    잡는지 별도로 확인한다 — 위 테스트가 subscript 유무와 순서 두 가지를
    한꺼번에 바꿔 어느 쪽 강화가 실제로 효과가 있었는지 구분이 안 되는
    것을 막는다."""
    offending_file = tmp_path / "some_feature.py"
    offending_file.write_text(
        "if requester_id == approver_id:\n    raise SelfApprovalError()\n",
        encoding="utf-8",
    )

    offenders = _find_offenders(tmp_path)

    assert len(offenders) == 1


def test_unrelated_equality_comparisons_are_not_flagged(tmp_path: Path) -> None:
    """넓힌 패턴(subscript/역순 허용)이 과도하게 넓어져, actor/counterparty와
    무관한 일반적인 동등 비교까지 오탐하지 않는지 확인한다."""
    clean_file = tmp_path / "unrelated.py"
    clean_file.write_text(
        "if order_id == other_order_id:\n"
        "    pass\n"
        "if existing['state'] == 'REQUESTED':\n"
        "    pass\n",
        encoding="utf-8",
    )

    assert _find_offenders(tmp_path) == []
