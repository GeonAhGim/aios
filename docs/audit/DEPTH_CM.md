# DEPTH 감사 — CM done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2725, qa-4)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/CM-/`에 맞는 리프 전수(15건, 60건 이하이므로 전수 대조).
- 축 하한: 제목이 `CM-`로 시작하는 리프(13건)는 ADR-2026-09-09-C의 안전축(R, L4, LA/LB/LC, FA, CM, EO, DC) 중 CM(컴플라이언스)에 해당 → **D3 하한**. 제목이 `CM-`으로 시작하지 않고 본문 중 다른 리프 번호를 괄호 등으로 언급만 하는 경우(1756, 2099)는 축 코드 목록에 없음 → **D2 하한**.
- 판정 기준(ADR-2026-09-09-C): D0=스텁, D1=happy path+단위 테스트, D2=경계·실패·동시성·복구 테스트 **전부** 충족(negative≥3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1), D3=D2 + 적대적/리플레이/다중 인스턴스 증명.
- 방법: 각 리프의 커밋(`git show <commit> --stat`)과 테스트 파일을 직접 열어 negative 테스트 개수·실패 주입·성능 단언·게이트 적색 재현·적대적/동시성 증명 여부를 실측했다. 코드 수정 없음.

## 요약

- 전체 15건 중 **15건 전부 자기 축의 D 하한 미달**(meets_floor=false 15/15).
- 등급 분포: D0 1건 · D1 14건 · D2 0건 · D3 0건.
- D3 하한(CM-) 대상 13건 중 미달 13건. D2 하한(비축, 1756/2099) 대상 2건 중 미달 2건.
- 가장 흔한 단일 누락 항목: **수치 성능 단언 부재**(15/15 전부). 다음으로 D3의 적대적/다중 인스턴스/리플레이 증명 부재(13/13, CM-8·CM-5만 부분적 우회-배선 증명 보유). CM 축은 대부분 순수 도메인 함수(I/O 없음)라 L4/BR 축과 달리 "실패 주입"이 monkeypatch 예외 시뮬레이션(CM-3/CM-11/CM-14)이나 실 DB WORM 트리거 위반(CM-4)으로만 제한적으로 충족된다.
- 완전 미시작/유령 done 성격 1건: task-2099는 P0 alembic head 병합 커밋으로 코드·테스트 변경이 전혀 없는 no-op 마이그레이션 정리 작업 — 정규식 `/CM-/`이 커밋 메시지 괄호 속 "CM-4" 언급에 우연히 매치되었을 뿐, 실제로는 CM 축 리프가 아니다(D0).
- task-1756(PLT-43)도 같은 이유로 매치됨(본문에 "CM-5" 언급) — 실질은 직무분리 원시타입(segregation_of_duty) 구현이며, 정적 재발명-금지 회귀 가드(test_segregation_of_duty_static.py)를 갖춰 유사 리프들보다 게이트 적색 재현 요소는 있으나 negative<3.

## 전수 판정표

| id | title | commit | axis | grade | floor met | evidence | missing (D2/D3 미달 근거) |
|---|---|---|---|---|---|---|---|
| 1756 | PLT-43 직무분리 원시타입 (CM-5·브레이크글라스가 각자 재발명 중) | 203c1ca | D2 | D1 | **NO** | test_segregation_of_duty.py: 4 tests, negative=2 (test_rejects_identical_actor_and_counterparty, test_rejects_identical_non_uuid_ids); test_segregation_of_duty_static.py: 2 AST 기반 회귀 가드(재발명 금지 스캔 + known-call-site import 확인, 게이트 적색 재현에 준함) | negative<3(2건뿐), 실패 주입 테스트 없음(DB/네트워크/크래시 시뮬레이션), 수치 성능 단언 없음 |
| 2035 | CM-1 mandates contracts/v1 확장: ComplianceDecision·RuleHit(기존 policy_decision 매핑, 신규 테이블 0) | 68c264ad | D3 | D1 | **NO** | test_contracts_v1.py: 6 tests, negative=3(verdict 범위 밖 거부·비-sha256 hash 거부·naive datetime 거부); 순수 dataclass/매퍼 검증, I/O 없음 | 실패 주입 없음, 수치 성능 단언 없음, 게이트/CI 적색 회귀 테스트 없음, D3 적대적/다중 인스턴스/리플레이 증명 없음 |
| 2036 | CM-2 MandateRuleInput 제약 7종 확장 + 배제 규칙(자산군·국가·통화·유동성·ESG) | d84f0fed | D3 | D1 | **NO** | test_exclusion_rules.py: 9 tests, negative=2(ESG 제외 심볼 DENY, 집중도 초과 DENY); 경계·조합 테스트는 존재하나 거부 단언은 2건뿐 | negative<3(2건뿐), 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 적대적/다중 인스턴스 증명 없음 |
| 2037 | CM-3 mandates domain/rule_bundle.py + evaluator.py (순서 무관 최악 판정·번들 해시) | 82a1206d | D3 | D1 | **NO** | test_rule_bundle_evaluator.py: 11 tests, negative=3(duplicate_rule_id 거부, naive datetime 거부, 규칙 예외 fail-closed DENY); test_rule_exception_is_fail_closed_deny_not_swallowed_uncaught = 실패 주입(규칙이 예외를 던져도 삼켜지지 않고 DENY로 fail-closed됨을 실증); decision_id 결정성 테스트(리플레이 재현성, CM-A4)는 있으나 단일 프로세스 한정 | 수치 성능 단언 없음, 게이트/CI 적색 회귀 테스트 없음, D3 다중 인스턴스/리플레이 증명 없음(결정성 테스트는 단일 프로세스뿐) |
| 2064 | CM-4 policy_bundle·policy_decision 부족분 추가(WORM 트리거 확인) + 어댑터 확장 + 통합 | 93ff9431 | D3 | D1 | **NO** | test_policy_repository.py(실 DB): negative=3(policy_decision UPDATE/DELETE가 asyncpg.RaiseError append-only violation으로 거부, policy_bundle 위조 UPDATE 거부 — 실 DB 제약 위반으로 실패 주입+게이트 적색 재현에 준함); test_insert_policy_bundle_race_...는 순차 이중 호출 멱등성 검증으로 진짜 동시 다중 워커 경합이 아님 | 수치 성능/처리량 단언 없음; race 테스트가 순차 호출이라 D3의 적대적/다중 인스턴스 증명 미충족 |
| 2065 | CM-6 mandates domain/rules/{restricted_list,concentration}.py (금지종목·집중도, 순수 규칙) | 9f5e7ba1 | D3 | D1 | **NO** | test_restricted_list.py(7)+test_concentration.py(6): negative≥5(누락 필드 fail-closed, 대소문자 구분, 한도 초과 DENY 등); 순수 함수, I/O 없음 | 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 적대적/다중 인스턴스 증명 없음 |
| 2066 | CM-7 mandates domain/rules/{leverage,liquidity,position_limit}.py (레버리지·유동성·포지션 한도) | 59777d42 | D3 | D1 | **NO** | 3개 테스트 파일 각 negative≥5(경계·누락 필드·non-decimal fail-closed); liquidity 파일은 ADV 0/음수 division-by-zero 방어를 명시적으로 검증 | 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 적대적/다중 인스턴스 증명 없음 |
| 2099 | P0 전 워커 정지 재발: alembic head 2개(a2c4f9e1b3d5 FA-10 · c6a3d8f14b92 CM-4) 병합 리비전 | a3096b99 | D2 | D0 | **NO** | 단일 alembic merge 리비전 파일뿐, 로직 변경 없음, 테스트 파일 추가/변경 0건; 커밋 메시지 자체가 "두 브랜치 모두 겹치는 테이블이 없는 no-op merge"라고 명시 | 테스트 증거 전무; 정규식이 커밋 메시지 괄호 속 "CM-4" 언급에 우연히 매치되었을 뿐 실질적 CM 리프가 아님 — D2/D3 증빙 요소 전부 부재 |
| 2105 | CM-8 application/evaluate_pre_trade.py + 주문 경로 배선(submit_order compliance_decision_id 필수) + 적대적(우회 0) | 52e13ef4 | D3 | D1 | **NO** | test_evaluate_pre_trade.py(8, negative=4: mandate 없음/paused revision/restricted 심볼/무관 테넌트 전부 거부) + test_no_bypass.py(4, test_removing_compliance_wiring_lets_forbidden_symbol_through는 컴플라이언스 호출을 stub으로 치환하면 방금 거부된 시나리오가 통과로 뒤집힘을 실증하는 배선 증명, forged-gate 2차 방어선 테스트도 존재) | 실패 주입 테스트 없음(monkeypatch는 배선 증명이지 DB/네트워크 결함 주입이 아님), 수치 성능/지연 단언 없음, D3 다중 인스턴스/리플레이 증명 없음 |
| 2118 | CM-5 위임장 개정 거버넌스 승격(작성자≠승인자 강제) — activate_revision·propose_amendment | 365c1e56 | D3 | D1 | **NO** | test_revision_governance.py(3, 자기승인 거부) + test_self_approval_blocked.py(2, 디코이 개정안을 끼워 넣어도 자기승인 여전히 거부되는 적대적 우회 시도 테스트 + assert_actor_not_counterparty를 no-op으로 치환하면 통과로 뒤집힘을 실증하는 배선 증명) | negative<3(거부 단언 2건뿐), 실패 주입 없음, 수치 성능 단언 없음; 디코이 테스트만으로는 D3의 다중 인스턴스/리플레이 증명에 못 미침 |
| 2458 | CM-9 mandates domain/rules/{short_sale,wash_trade}.py — 공매도·자전거래 순수 규칙 2종 | 147315c8 | D3 | D1 | **NO** | test_short_sale.py(11)+test_wash_trade.py(10): negative≥6(대차 불가 매도 거부, KRX 업틱 위반 거부, 누락 필드 fail-closed x4, 동일테넌트 교차 거부/타테넌트 교차 통과); 두 모듈 모두 datetime/random/httpx 미임포트를 AST로 검증(순수성 가드) | 실패 주입 없음, 수치 성능 단언 없음, 게이트/CI 적색 회귀 테스트 없음(순수성 가드는 별개), D3 적대적/다중 인스턴스 증명 없음 |
| 2460 | CM-10 mandates domain/market_abuse.py — 시장질서 3패턴 사후 탐지(자전거래·종가 관여·호가 조작 의심) | 5e988ff4 | D3 | D1 | **NO** | test_market_abuse.py: 19 tests, negative≥4(fills/market_close_at/orders 누락 및 하위 필드 누락 전부 fail-closed); 3패턴 각각 정확한 경계값 커버리지; CM-9와 로직 미공유 정적 검증 | 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 회귀 테스트 없음, D3 적대적/다중 인스턴스 증명 없음 |
| 2509 | CM-11 application/evaluate_post_trade.py — 체결·일마감 사후 배치 판정 + 위반 시 차단 | 8605bfd6 | D3 | D1 | **NO** | test_evaluate_post_trade.py(8, negative≥3: 자전거래 체결 차단·무차입 공매도 차단·시장남용 데이터누락 fail-closed) + test_market_abuse_exception_fails_closed(monkeypatch)는 탐지 도중 예외를 시뮬레이션해 fail-closed 차단을 실증하는 진짜 실패 주입; test_post_trade_batch.py(4, 실DB 배치 통합 + 재실행 멱등 dedup + background_loops 배선 증명) | 수치 성능/처리량 단언 없음(스케줄 배치인데도 지연·처리량 임계 단언 부재), 게이트/CI 적색 회귀 테스트 없음, D3 다중 인스턴스/리플레이 증명 없음(단일 프로세스 배치뿐) |
| 2510 | CM-13 application/explain.py — 컴플라이언스 판정 설명 재현(같은 입력 → 같은 판정) | 662f7c2d | D3 | D1 | **NO** | test_explain.py: 8 tests, negative=4(번들 해시 드리프트 거부, 미상 decision_id 거부, 참조 번들 누락 거부, 참조 revision 누락 거부); 반복 호출 바이트 동일성 증명(설명가능성 요구사항) + 절대 변형/재삽입 없음 불변식 증명 | 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 회귀 테스트 없음, D3 적대적/다중 인스턴스/리플레이 증명 없음 |
| 2526 | CM-14 reporting/domain/best_execution.py — 체결가 vs 벤치마크 최선집행 근거(EM-12 위임, 재구현 금지) | f18d1a20 | D3 | D1 | **NO** | test_best_execution.py: 7 parametrized tests, negative≥4(벤치마크 필드 3종 누락 거부, route 누락 거부, 순서 오류 거부, 잘못된 숫자 거부); test_delegation_failure_propagates는 EM-12 위임 함수가 RuntimeError를 던지도록 monkeypatch해 예외가 삼켜지지 않고 전파됨을 실증하는 진짜 실패 주입 | 수치 성능 단언 없음, 게이트/CI 적색 회귀 테스트 없음, D3 적대적/다중 인스턴스/리플레이 증명 없음 |

## 발행된 DEEPEN task

D3(CM-)/D2(비축) 미달 15건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 1, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id):

| 원 task id | DEEPEN task id |
|---|---|
| 1756 | 2853 |
| 2035 | 2854 |
| 2036 | 2855 |
| 2037 | 2856 |
| 2064 | 2857 |
| 2065 | 2858 |
| 2066 | 2859 |
| 2099 | 2860 |
| 2105 | 2861 |
| 2118 | 2862 |
| 2458 | 2863 |
| 2460 | 2864 |
| 2509 | 2865 |
| 2510 | 2866 |
| 2526 | 2867 |
