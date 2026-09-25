# ADR-2026-09-26-A: MVP-2 우선 2의 클라우드 전용 조건부 선착수 (ADR-2026-09-24-A Decision 1-1 개정)

## Status
Accepted (2026-09-26, Chief Architect). 사용자 승인 2026-09-26 "권장하는대로 진행해"(지식베이스 세션 경유).
ADR-2026-09-24-A Decision 1-1("MVP-2 리프는 MVP-1_CLOSEOUT.md 생성 전 발행 금지")을 아래 조건 안에서만 완화한다.

## Context
로컬 워커풀은 MVP-1 종결(CI 녹색·종결 게이트)에 전념해야 하지만, 클라우드 세션(Claude Code on the web,
별도 $250 크레딧, 2026-11-05 만료)은 상시 진행 ≥10건·대기 ≥20건으로 돌리라는 사용자 지시가 있다.
MVP-1 잔여만으로는 클라우드 적합 리프 공급이 부족하다(2026-09-26 실측: 후보 2건). MVP-2 우선 2
(ADR-2026-09-09-B Decision C: M2-3·5·6·7·10·13·16, U-5·6·7·8·9·11·16) 가운데 MVP-1과 파일이 겹치지 않는
독립 모듈은 저장소만으로 완결되고 fake로 검증 가능해 클라우드에 맞는다. 크레딧은 만료되면 소멸한다.

## Decision
1. **범위**: MVP-2 우선 2 중 **MVP-1 파일과 겹치지 않는 독립 모듈만**. 제외: 주문·돈 경로(S등급 상태
   전이), closeout_check 대상 경로, 마이그레이션이 겹치는 항목. 우선 3은 계속 보류.
2. **기능 플래그 기본 off 필수**(§U 공통 DoD). M2 항목도 사용자 노출이면 플래그(`FF_*`)를 붙인다.
3. **클라우드 전용**: 발행 즉시 `task_update.py --hold cloud_session`. 로컬 레인·Claude 워커풀에 배정하지
   않는다. 리프 제목은 `[cloud-early]` 접두, task 필드 `cloud_early: true`. task-2625(M2-HOLD)는 로컬용
   게이트로 그대로 둔다.
4. **QA 이중 부담 제거**: 클라우드 PR은 파이프라인 검증(병합 트리 ruff/mypy/대상 테스트/래칫 4종)을
   리뷰로 인정한다. 함대 후속 QA는 **S등급만** 발행하고 M/L은 생략한다(pm `handle_tier_followup`).
5. **main 보호**: main commit CI 적색이거나 MVP-1 closeout 진행 중이면 클라우드 PR 머지를 멈추고
   대기만 쌓는다(클라우드 파이프라인 규칙).
6. **해제**: `docs/milestones/MVP-1_CLOSEOUT.md`가 생기면 이 제한을 풀고 MVP-2 전체로 확대한다
   (task-2625 흐름 유지).

## Consequences
- PM_CYCLE_PROMPT §B -1(b)·(i)에 반영. 클라우드 대기열 원천에 `[cloud-early]` MVP-2 우선 2가 추가된다.
- pm orchestrator: 클라우드 출신 done 리프(hold_reason=cloud_session)의 후속 QA는 S만.
- 되돌림: 이 ADR을 Superseded로 바꾸고 §B -1(b)의 [cloud-early] 문단을 지우면 원래 규칙으로 복귀한다.
