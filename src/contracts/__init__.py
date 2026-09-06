"""SCAFFOLD zone placeholder — .aios-zone은 에이전트가 수정할 수 없다(P8).

전수감사 2026-09-06 P1-C(task-1722) — 이전 `enterprise.py`(EvidenceReference/
OrderIntent/PolicyDecision/StrategyPackage)는 src 임포터가
`src/services/paper_strategy_projection.py` 하나뿐이었고, 그마저도 실제
호출자가 없어(0 임포터) 둘 다 삭제했다 — `src/foundation/mandates/**`가
이미 같은 역할(전략 배포 거버넌스/정책 판정)을 실제로 배선된 상태로
제공한다. `.aios-zone`의 `SCAFFOLD: src/contracts/**` 패턴 선언은 사람만
고칠 수 있어(zone 정책 파일, P8) 지우지 않고, 매칭 대상만 이 빈 파일로
남긴다."""
from __future__ import annotations
