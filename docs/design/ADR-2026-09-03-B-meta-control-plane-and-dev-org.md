# ADR-2026-09-03-B: Meta-Control Plane과 개발 조직(Orchestrator·헤드리스 worker·PM·Guard)

## Status
Accepted (2026-09-03). 사용자가 구조를 제시하고 승인했다("승인, 시작해").

## Context
- 대화형 Claude 세션 6개가 worker로 일하던 방식은 컨텍스트 토큰을 빠르게 소모했고, 세션 간 상태가
  채팅에만 있어 재현·인수인계가 어려웠다. 사용자 원칙: **"프로젝트는 영속적, worker 컨텍스트는 일회성"**.
- 보안·아키텍처 검사를 worker 풀과 동급으로 두면 worker가 스스로 검사를 약화시킬 수 있다.
  AIOS 정책문서 16장(메타 통제면 불변)과 DevEngine ADR(자기 통제면 쓰기 금지)을 개발 조직 자체에
  적용해야 한다.

## Decision
```
Immutable Policy Layer (aios-meta/policy/immutable.md P1~P9)
  → Architecture Guard | Security Guard   (aios-meta/guards, veto/flag)
  → Task Orchestrator (C:\aios\pm\orchestrator.py — 큐·의존성·풀·재시도·Guard·QA→Review 체인)
  → Worker Pools (backend·frontend·qa·reviewer; 모델·크기는 pools.yaml)
  → GitHub (origin/main) → PM(Opus 헤드리스 pm_cycle.py: 결정·배정) → 아키텍처 영향 시 Chief Architect(Fable)
```
1. **Meta-Control Plane은 별도 public 저장소 `GeonAhGim/aios-meta`**에 두고, `enforce_admins`+CODEOWNERS
   리뷰 필수로 어떤 에이전트(PM·Chief Architect 포함, 같은 GitHub 계정이라도)도 직접 push할 수 없다.
   변경은 PR을 사람이 웹에서 승인·병합할 때만. mihwa-aios CI의 `guards` job은 이 저장소를 **고정 SHA**
   (`META_GUARDS_REF`)로 checkout해 실행한다.
2. **상태는 파일에만**: `C:\aios\pm\tasks\task-<id>.json`(PROTOCOL.md 스키마)이 유일한 진실.
   worker·PM은 `claude -p` 한 턴으로 살고 죽으며, 대화형 세션은 worker가 아니다.
3. **worker 격리**: worker마다 git worktree(`C:\aios\wt\<worker>`)와 테스트 DB(`aios_test_<worker>`).
   공유 인덱스 사고를 구조적으로 제거.
4. **Guard는 두 번**: worker가 push 전에, Orchestrator가 push 후에. veto는 `needs_decision`+에스컬레이션,
   flag는 Chief Architect 검토. Guard 코드는 결정론적 검사만(모델 없음).
5. **체인**: implement → QA(독립 환경에서 게이트·DoD 대조, 소규모 수정 가능) → Review(수정 금지, 판정만).
6. **역할 경계**: Chief Architect는 원칙·ADR·에스컬레이션 판단만, PM은 분해·배정·결과 판단만,
   Orchestrator는 기계적 스케줄링만. 사람은 Chief Architect와만 대화한다.

## Consequences
- 토큰 비용은 task 단위로 상한이 잡히고, 실패는 task 파일에 남아 재시도·재현이 가능하다.
- Guard의 P6(300줄)·P5(계약 호환)·P2(LIVE 가드)·P3(WORM)·P4(시크릿)이 사전 존재 위반까지 잡으므로,
  초기에 오래된 파일들이 veto를 받는다(예: `main.py` 331줄 → task-117로 분할). 이는 의도된 마찰이다.
- 에이전트 계정 분리는 아직 불가(사용자 계정 하나). 자기 PR 승인 불가 규칙이 그 공백을 메운다.

## Rejected
- 보안을 worker 풀로 두는 안(사용자 명시 반려).
- 프롬프트 규칙만으로 통제면을 보호하는 안: 쓰기 권한의 물리적 부재만이 보장이다.
- Agent SDK 상주 데몬: 현 단계에선 `claude -p`+파일 프로토콜이 더 단순하고 검증 가능. SDK는 후속 옵션.
