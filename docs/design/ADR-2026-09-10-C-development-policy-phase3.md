# ADR-2026-09-10-C: 개발정책 3단계 전환 — 파일 길이에서 도메인 응집·불변식 지역성으로, 변경 거버넌스는 closeout 시점에

## Status
Accepted (2026-09-10, CTO; 사용자 승인). 출처: `docs/blue_team/09-current-aios-implementation-review-and-development-policy.md`(REVIEW)와
`docs/ideabank/2026-09-10/01-development-policy-evolution.md`(IDEA → 이 ADR로 승격). ADR-2026-09-09-C(깊이 하한)·D(변곡점)·F(등급 사다리)를 보완한다.

## Context
- AIOS는 초기 스캐폴드 단계가 아니라 리스크·mandate·OMS·원장·증거·실행 소유권·리플레이가 실제로 배선된 "도메인 완결·실패 의미론" 단계(Phase 3)다.
- 300줄 규칙은 CI 게이트가 아니라 워커·리뷰 프롬프트에만 있는 규칙이다(확인: scripts/·.aios-zone·quality.yml에 LOC 게이트 없음). 초기 구조 폭주 방지에는 유효했지만,
  이제는 에이전트가 줄 수를 최적화하느라 안전 불변식(소유권→mandate→리스크→컴플라이언스→kill switch→멱등→상태 전이→outbox→어댑터)을 여러 파일로 흩는 역효과가 크다.
- 아키텍처는 보존 대상이다. 변경은 불변식 위반·경계 오류·권한 중복·우회·측정된 병목이 증명될 때만 허용한다.

## Decision 1 — 개발 단계 모델과 현재 위치
| 단계 | 초점 | 대표 정책 |
|---|---|---|
| 1 구조 폭주 방지 | 작은 리프·LOC 상한·엄격 zone | (지남) |
| 2 도메인 형성 | 소유권·애그리거트·계약 | (지남) |
| **3 도메인 완결·실패 의미론** | 응집·불변식 지역성·실패 주입·동시성·리플레이·게이트 적색 증명·fail-closed | **현재** (MVP-1 후반) |
| 4 LIVE 하드닝 | DR·PITR·알림·HA·환경 분리·배포·변경 거버넌스·컴플라이언스 런타임 강제 | H-1~H-14 |
| 5 상용·기관 | SLO·마이그레이션 규율·호환성·테넌트 격리·버전 계약·비용·감사 증거 | MVP-3 이후 |

## Decision 2 — 파일 길이 정책
- 300줄 "초과 금지"를 폐지한다. 파일은 줄 수가 아니라 **bounded context·애그리거트·capability·불변식 소유권·독립 변경축·의존 방향·계약·안전 경계**로 나눈다.
- 단계형 관찰 지표(에이전트 비대화 방지용 상한은 유지): **500줄 경고**(리뷰어가 책임 혼합 확인), **800줄 아키텍처 리뷰 트리거**(질문: 책임 둘 이상? 독립 변경축? 공개/비공개 분리? 테스트 가능성? fan-out?),
  **1,000줄 하드**(생성 테이블·프로토콜 매핑·결정론 규칙 행렬은 파일 상단 `loc-allow: <사유>`로 예외).
- 금지: 안전 불변식을 LOC 준수 목적으로 분산, `utils.py`/`helpers.py`/`common.py`에 도메인 권한 숨기기, 항상 같이 바뀌는 코드의 억지 분리.
- **기존 파일 대규모 병합 금지.** 해당 도메인을 실제로 수정하는 task에서 인위적 분절이 발견될 때만 국소 통합(ADR-D 리팩터링 게이트 적용).

## Decision 3 — LOC 대신 CI가 보는 것
복잡도(cognitive complexity 상한, 깊은 중첩), 의존(순환·금지 방향: domain→adapter 역의존, foundation 경계 침범; import-linter), 권한(리스크·mandate·멱등 권한 중복, 로컬 우회 — CONSIST-1 항목),
상태(숨은 전역 가변 상태·트랜잭션 소유 불명), 안전(fail-open·negative 부재·실패 주입 부재·게이트 적색 증명 부재), 공개 API(계약 비호환 변경). 각각 baseline 래칫으로 도입한다(ADR-2026-09-09-E OPS-42 규약: warn → gate).

## Decision 4 — 깊이 체크리스트의 경계(ADR-2026-09-09-C 보완)
D2/D3 항목(negative·실패 주입·성능·게이트 적색·동시성·리플레이)은 **해당 실패 모드가 실제로 존재하는 코드에만** 요구한다. 없으면 QA가 note에 `N/A(<사유>)`를 적고 통과시킨다.
동시성이 핵심인 곳(outbox 워커·unknown 해소기·브로커 디스패치·mandate 캐시)은 반드시 깊게 본다. 체크리스트가 목적이 되는 것을 금지한다.

## Decision 5 — 변경 거버넌스(H-14)와 "현재 HEAD 독립 녹색"
- MVP-1 동안은 워커 직접 push + 로컬 CI(1차) + Actions 3시간 주기(2차)를 유지한다(처리량).
- **H-14(하드닝 항목 추가)**: MVP-1 closeout 시점에 main 브랜치 보호(PR-only·force push 금지·필수 체크·가드 체크)를 켜고 워커 흐름을 브랜치→PR→자동 머지로 전환한다. 에이전트 권한은 branch·commit·PR까지.
- closeout_check에 **"현재 main HEAD가 GitHub Actions에서 녹색"** 항목을 추가한다("최근 어떤 커밋이 녹색"과 구분).

## Decision 6 — MVP-2 병행 규칙 보정
주문 경로 위에 얹히는 MVP-2 리프(U-4 룰 엔진의 주문 행동, M2-4 bracket/OCA)는 H-1(mandate 강제)·H-11 완료를 선행조건으로 한다. 데이터·화면만 건드리는 리프(WS 게이트웨이·스크리너·통합 손익·온보딩·AI 어시스턴트 생성/설명)는 병행을 유지한다.

## Decision 7 — Idea Bank 취급
`docs/ideabank/**`는 비규범이며 워커의 구현 근거가 될 수 없다(CLAUDE.md에 명시). 01은 이 ADR로 승격, 02(BYOAI 커넥터)는 ADR-2026-09-05-A 구체화 후보로 REVIEW. 03·04·06은 MVP-1 하드닝 후, 05(결제·정산·AML)와 03의 Signal/Automation/Managed 상품 규제 분류는 전문가 검토 전 착수 금지(human_blocked HB-10).

## Consequences
- 프롬프트·리뷰 규칙에서 300줄 문구 제거, CLAUDE.md(CLAUDE-1/2)에 Decision 2·3·7 반영, 래칫 게이트 3종(복잡도·의존·권한) 신설, H-14·closeout 항목 추가, U-4a·M2-4 의존 추가.
