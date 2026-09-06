"""PLT-43 정적 검사 — actor≠counterparty 비교를 이 모듈 밖에서 재발명하지
않는지 단언한다.

ADR-2026-09-06-G §9: "'작성자≠승인자'가 기능마다 따로 구현된다" 문제를
막기 위해 `segregation_of_duty.assert_actor_not_counterparty()`를 유일한
정의로 둔다. 이 테스트는 두 가지를 확인한다.

1. `src/` 전체에 이 모듈 밖에서 actor/approver 계열 식별자와 counterparty/
   requester/proposer 계열 식별자를 직접 `==`/`!=`로 비교하는 코드가 없다
   (있으면 인라인 재발명 — 이 모듈을 거치도록 고쳐야 한다).
2. 이미 실존하는 실제 호출처(`core/approval/service.py`의 DUAL 2차 서명
   검사)가 이 모듈을 실제로 import한다.

미검증(2026-09-07 기준, `segregation_of_duty.py` docstring과 동일 근거):
CM-5(`mandates/application/activate_revision.py`·`propose_amendment.py`)와
PLT-35(`core/security/break_glass.py`)는 이 저장소에 아직 존재하지 않는다
— 존재하지 않는 파일은 스캔 대상이 될 수 없으므로, 이 테스트는 "오늘
존재하는 코드는 재발명하지 않는다"만 보증한다. 두 리프가 나중에 만들어질
때 인라인으로 재발명하면 이 정적 검사가 그 즉시 실패한다.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[4] / "src"

_EXEMPT_SUFFIXES = ("segregation_of_duty.py",)

# actor측: 이 action을 수행하려는 주체. counterparty측: 이미 그 action에
# 관여한(또는 그 action의 대상인) 다른 주체. 두 측을 직접 == / != 로
# 비교하는 건 segregation_of_duty 모듈이 독점해야 하는 판단이다.
_ACTOR_SIDE = r"\w*(?:actor|approver|admin_id|signer)\w*"
_COUNTERPARTY_SIDE = r"\w*(?:counterpart|requester|proposer|requested_by|proposed_by|approver)\w*"
_REINVENTION_PATTERN = re.compile(
    rf"\b{_ACTOR_SIDE}\s*(?:==|!=)\s*{_COUNTERPARTY_SIDE}\b"
)


def _iter_src_files() -> list[Path]:
    return [
        p
        for p in SRC_ROOT.rglob("*.py")
        if not p.name.endswith(_EXEMPT_SUFFIXES) and "__pycache__" not in p.parts
    ]


def test_no_inline_reinvention_outside_segregation_of_duty_module() -> None:
    offenders: list[str] = []
    for path in _iter_src_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _REINVENTION_PATTERN.search(line):
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "actor != counterparty 비교는 "
        "src/foundation/trust/domain/rules/segregation_of_duty.py의 "
        "assert_actor_not_counterparty()를 거쳐야 합니다. 재발명 발견:\n"
        + "\n".join(offenders)
    )


def test_known_call_site_imports_segregation_of_duty() -> None:
    approval_service = SRC_ROOT / "core" / "approval" / "service.py"
    text = approval_service.read_text(encoding="utf-8")
    assert "trust.domain.rules.segregation_of_duty import" in text
    assert "assert_actor_not_counterparty" in text
