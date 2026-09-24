# ADR-2026-09-24-A: MVP-1 종료 순서(검증 우선)와 함대 조직의 재사용 자산화(DevEngine 기초)

## Status
Accepted (2026-09-24, Chief Architect). 사용자 지시 "권고 순서대로 진행하고, 함대 조직은 추후 DevEngine
개발/구현의 기초로 활용되도록 별도로 모아두고, 다음 프로젝트를 함대로 개발할 때 참고해 쓸 수 있게 조치".

## Context
2026-09-24 냉정한 평가: 명세 리프 482건 중 457 done(95%)이지만 장부와 저장소 실체가 어긋나 있었다
(commit 없는 done 수십 건, "이미 구현됨" 재발행 100건+, 종결 게이트 첫 실행에서 FAIL 6항, full CI
수일간 적색, D?(깊이 미판정) 리프 절반). 오늘 CTO 작업의 80%가 제품이 아니라 함대(오케스트레이터·
엔진·감시) 결함이었다 — 기계의 복잡도가 제품 검증을 뒤로 밀고 있다. 한편 이 함대 자체(자율 오케스트
레이션·자동 결정 규칙 ND-1~18·로컬 LLM 레인·헬스체크·종결 게이트)는 드문 자산이며, DevEngine과 다음
프로젝트의 기초가 돼야 한다.

## Decision 1 — MVP-1 종료 순서 (이 순서를 어기는 배정을 하지 않는다)
1. **CI 상시 녹색 + 종결 게이트 12항 PASS가 최우선 종료 조건.** MVP-2(M2-1~18, U-1~14) 리프는
   `docs/milestones/MVP-1_CLOSEOUT.md`가 생성되기 전엔 발행하지 않는다(pm task-2625 hold_gate
   closeout:MVP-1 유지). ci_red 수정 리프는 P0, `[CA P0]` 마커로 Claude 레인 즉시 착수.
2. **QA 부채는 로컬 레인으로 소진.** local-qa/local-xrev 5슬롯을 상시 채운다(pm ADR: 유연 계획
   a542fcf4). Claude QA는 S등급과 로컬 2회 실패분만.
3. **D?(깊이 미판정) 리프는 개별 재QA 대신 E2E 시나리오로 대체 검증.** 아래 5개 시나리오(Decision 3)가
   녹색이면 관련 영역의 D? 리프는 "E2E로 검증됨"으로 본다(spec_status가 depth=E2E로 표기).
4. **함대 개선은 "제품 검증을 막는 결함"으로 범위를 좁힌다.** ops 리프 발행 기준: CI/게이트/워커풀
   가동을 막는 결함, 사용자 지시, 보안. 그 외 함대 기능 추가는 MVP-1 closeout 이후.

## Decision 2 — 함대 조직의 재사용 자산화
- pm(C:\aios\pm)을 프로젝트 무관 **fleet kit**으로 분리해 보관한다: `GeonAhGim/aios-fleet-kit`
  (비공개). 경로·저장소·프롬프트를 파라미터화(`fleet.yaml`), 비밀(.env/.ghconfig)·런타임 상태
  (tasks/logs/ci/nightly)·프로젝트 고유 명세 제외, gitleaks 통과가 푸시 조건.
- `pm/docs/FLEET_HANDBOOK.md`: 아키텍처(CA→PM→orchestrator→pools→QA→reviewer→CI), 결정 규칙
  (ND-1~18, B-4), 레인 설계(로컬/클라우드/외부 엔진), 감시·복구(heartbeat/watchdog/selfreload),
  운용 규칙(task_update만·esc 수명주기 소유·BASELINE 승인), 그리고 2026-09-16~24 사고와 근본 수정
  목록을 "다음 프로젝트에서 처음부터 지킬 것"으로 정리한다.
- DevEngine은 이 kit을 기초로 삼는다: 첫 단계는 kit을 빈 저장소에 적용해 "명세→리프→구현→QA→
  리뷰→CI→closeout" 한 바퀴가 도는지 검증하는 드라이런.

## Decision 3 — E2E 시나리오 5종 (backend, S는 Claude 전용)
| id | 시나리오 | 등급 |
|---|---|---|
| E2E-1 | 페이퍼 주문 생명주기: 신호→리스크→컴플라이언스→주문→체결/취소/거부/만료→포지션·장부 반영 | S |
| E2E-2 | 워치독 LIQUIDATE: 시장 전반 급변→decide→liquidation_request→실행 루프 소비→WORM 기록 | S |
| E2E-3 | 시세 수집→저장→차트/지표 API: 틱·캔들 적재→리샘플→REST/WS 조회→참조 벡터 일치 | M |
| E2E-4 | 전략 스크립트→즉시 백테스트→리포트: DSL 컴파일→벡터 엔진→성과·오버피팅 지표→저장 | M |
| E2E-5 | 컴플라이언스 사후 판정→규제 보고서 생성→제출 포트 호출(모의)→감사 추적 | M |

## Consequences
- MVP-2 착수는 늦어지지만 "쓸 수 있는" MVP-1이 먼저 확정된다.
- 함대 kit은 pm의 변경을 따라가야 한다 — closeout 시점에 kit을 동기화하는 ops 리프를 반복 발행한다.
