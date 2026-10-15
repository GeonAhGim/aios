# 컴플라이언스·승인 적대적 감사 — task-12076

날짜: 2026-10-15. 기준 커밋: `48daeda40eb65c0465cf797f08390c4d1496c640`.
근거: ADR-2026-09-26-C(안정화 단계, §B -2(a)), docs/design/INVARIANTS.md I-01·I-02·
I-09·I-10·I-11, L4_compliance_and_regulatory_v1.0.md §9 CM-1~CM-12.
범위: `src/foundation/mandates/**`(application/evaluate_pre_trade.py·
evaluate_post_trade.py·activate_revision.py·create_draft_mandate.py·
propose_amendment.py·resolve_binding.py·pause_mandate.py, domain/evaluator.py·
rule_bundle.py·exclusion.py·market_abuse.py·rules/*), `src/core/approval/**`
(service.py·panic_prompt.py·_shared.py), `src/services/order_service/
foundation_compliance.py`·`foundation_gate.py`·`foundation_gate_mandate_layer.py`,
`src/services/approval_settings_service.py`, `src/core/portfolio/mandate_binding.py`,
관련 마이그레이션(189bdd25a017, d8e8e4ba2365, b3c7f19ad2e6, c9f4e2a1b6d7). 접합면으로
3곳의 실제 프로덕션 게이트 조립부(background_loops.py/execution_deps.py/oms/
application/wiring.py)와 `scripts/check_compliance_gate.py`를 확인했다. 선행 도메인
5종(주문 경로 task-7978, 원장·회계 task-8675, 리스크·서킷브레이커 task-8882, 인증·RLS
task-9420, 데이터 수집·재생 task-10436~10441)의 정정 착지를 전제로 마지막 6번째
도메인을 연다. 프로덕션·테스트 코드 변경 없음. 정정 리프 발행은 CTO/PM 소관이다.

## 0. 요약

| 발견 | 심각도 | 결론 | 증거 |
|---|---|---|---|
| F1 | S | CM-8 컴플라이언스 번들 게이트(`require_compliance_mandate`)가 세 프로덕션 조립부 전부에서 기본값(`False`)으로 남아, mandate를 구성하지 않은(기본 상태) 모든 테넌트의 주문이 restricted_list/concentration/leverage 규칙 평가 없이 통과한다 — 같은 파일의 숫자 mandate 레이어는 동일 부재 조건에서 무조건 DENY | `foundation_gate.py:111/234`, `foundation_compliance.py:81-85`, 대조군 통합테스트 `test_bypass_attempts.py:238-249`가 `control.allowed is True`를 재확인 |
| F2 | M | `policy_decision`(mandate 평가 WORM 기록)·`mandate_revision`·`approval_requests`에 RLS 정책이 없다 — task-9420/F1이 고친 "FORCE RLS 8개 테이블" 목록에 이 세 테이블이 빠졌고, 이들은 모두 `tenant_id`/`user_id`를 직접 갖고 조회 WHERE절에만 의존한다 | `b3c7f19ad2e6_rls_policies_foundation.py:42-55`, `postgres_policy_repository.py:88-122`, `189bdd25a017_approval_requests.py` |
| F3 | L | `propose_amendment`의 `revision_no` 할당이 `max(...)+1` 읽기 후 삽입이라 동시 개정 경합 시 `UNIQUE(mandate_id, revision_no)` 위반이 도메인 예외로 변환되지 않고 raw `asyncpg.UniqueViolationError`로 호출부까지 올라간다 — 데이터 손상은 없다(fail-closed) | `propose_amendment.py:65-66`, `postgres_repository.py:150-172`(대조: `get_or_create_mandate`:104-124는 같은 파일에서 이미 UniqueViolationError를 잡는다) |

**S 1건 / M 1건 / L 1건. 발견 0건이 아니므로 도메인 통과 불가(§B -2 2회 연속 0건
조건 미충족).** S는 추가 신뢰 경계 파괴 없이(그냥 mandate를 설정하지 않은 기본 상태로
주문을 내는 것만으로) CM-6/7 규칙군이 통째로 평가되지 않는 경우로 보았다. M은 현재
운영 DB 역할이 superuser(RLS bypass)라 즉시 악용되지는 않지만, F1(task-9420)이 이미
한 번 고친 것과 같은 유형의 방어심층 결손이 반복된 경우. L은 fail-closed가 유지되는
가용성/견고성 결함이다.

## 1. 선례·계약 대조

| 안전 경로의 선례/계약 | 컴플라이언스·승인 관찰 | 판정 |
|---|---|---|
| `foundation_gate_mandate_layer.py:64-78`(숫자 mandate 레이어)는 `NoActiveMandateError`를 무조건 DENY로 번역 — 끄는 플래그가 없다 | `foundation_compliance.py:81-85`(CM-8 규칙 번들 레이어)는 같은 "mandate 없음" 조건을 `require_compliance_mandate`(기본 `False`) 뒤에 숨긴다 | F1 |
| H-1b(task-3369)가 숫자 mandate의 `require_mandate`를 세 조립부 전부 `True`로 전환한 전례(review-safety 스킬 "이 축에서 났던 사고" 4번째 항목) | 같은 세 조립부(`background_loops.py:216`, `execution_deps.py:29`, `wiring.py:90`)가 `require_compliance_mandate`는 호출하지 않고 함수 기본값(`False`)에 맡긴다 | F1 |
| `scripts/check_compliance_gate.py`는 CM-A1("컴플라이언스 판정 없이 제출되는 경로 없음")을 "호출됐는가/두 id를 다 확인하는가"로만 검사 | 호출은 되지만 그 결과가 실질적으로 ALLOW-패스스루가 되는 플래그 상태는 이 스캐너의 검사 대상이 아니다 | F1(검사 공백) |
| `domain/evaluator.py:35-44`·`domain/rules/short_sale.py:56-105`는 평가 중 예외/결손 필드를 전부 DENY로 접는다(I-09 CM-A2) | 확인함 — 새 발견 없음, 선례와 일치 | 해당 없음 |
| `postgres_repository.py:194-260`(`activate_revision`)은 관찰한 `expected_active_revision_id`로 CAS, 트랜잭션 내부 재조회 없음(모듈 docstring이 그 이유를 직접 설명) | 확인함 — 선례와 일치, 새 발견 없음 | 해당 없음 |
| `service.py:174-246`(`approve`)는 SOLO/DUAL 양쪽 모두 `WHERE status='PENDING'`/`first_approver_id IS NULL` 조건부 UPDATE로 동시 승인·동시 2차서명을 직렬화 | 확인함 — 선례와 일치, 새 발견 없음 | 해당 없음 |
| task-9420/F1: "FORCE RLS 테이블의 repository가 GUC 바인딩을 누락해 실제로는 보호되지 않는다"는 지적을 받고 8개 테이블로 확정 | `policy_decision`/`mandate_revision`/`approval_requests`는 그 8개 목록 자체에 없다 — 바인딩이 아니라 정책 자체가 없는, 한 단계 더 이전의 결손 | F2 |

CM-8의 규칙 번들(`assemble_rule_bundle`, `evaluate_pre_trade.py:71-141`)이
`projected_instrument_pct`/`projected_gross_leverage`/`order_notional` 등 스냅샷 키가
없으면 concentration/leverage/liquidity/position_limit 규칙을 애초에 포함하지 않는
것은 해당 모듈 docstring(15-21행)과 `foundation_compliance.py:38-48`의
`_compliance_snapshot`이 이미 "검증되지 않은 미구현"으로 공개 문서화한 기존 스콥
축소다 — 이번 감사의 신규 발견으로 세지 않는다(§B -2 기준: 이미 추적 중인 격차).
단, F1은 이 문서화된 격차와 별개로 "mandate가 전혀 없는 테넌트"(restricted_list조차
평가되지 않는 조건)가 실제로 기본 허용된다는, 별도의 조립부 배선 문제다.
`FROZEN_PAPER_ONLY`(core/strategy·portfolio·risk·executor) 수정 없음 — 범위에 든
`core/portfolio/mandate_binding.py`는 읽기만 했고(bind() 해시 재검증·fail-closed 확인,
새 발견 없음), 수정하지 않았다.

## 2. 발견과 재현

### F1 — CM-8 컴플라이언스 게이트가 모든 프로덕션 조립부에서 기본 허용 (S)

위치: `src/services/order_service/foundation_gate.py:102-111`(
`make_foundation_pre_submit_gate`의 `require_compliance_mandate: bool = False` 기본값)
및 `:231-236`(그 값 그대로 `evaluate_compliance_gate`에 전달), `src/services/
order_service/foundation_compliance.py:55-96`(`ComplianceMandateMissingError`를
잡아 플래그가 `False`면 `ComplianceGateResult(True, (), marker)`로 통과), 그리고
세 실제 프로덕션 조립부 — `src/services/background_loops.py:215-216`,
`src/api/execution_deps.py:29`, `src/services/oms/application/wiring.py:90` — 전부
`make_foundation_pre_submit_gate(pool, require_mandate=True)`만 호출하고
`require_compliance_mandate`는 넘기지 않아 기본값 `False`로 굳어 있다. `grep -rn
"require_compliance_mandate=True" src`는 프로덕션 코드에서 0건이고, 테스트에서만
명시적으로 `True`를 준다(`tests/unit/services/test_foundation_compliance.py`,
`tests/adversarial/compliance/test_bypass_attempts.py`).

재현(정적 + 기존 테스트 실행 결과): mandate를 한 번도 만들지 않은 신규 테넌트는
`create_draft_mandate`/`propose_amendment`/`activate_revision` 중 어느 것도 호출된
적이 없으므로(API가 자동 프로비저닝하지 않음, `src/api/routers/foundation/
mandates.py`가 유일한 생성 경로) `evaluate_pre_trade`가 `ComplianceMandateMissingError`
를 던진다. `tests/adversarial/compliance/test_bypass_attempts.py:238-249`의
"대조군"(`no_mandate_tenant`, `require_compliance_mandate=False`)이 이 경로를 그대로
실행해 `control.allowed is True`를 통과시킨다 — 즉 CM-6(restricted_list)이 전혀
평가되지 않은 채 주문이 risk/numeric-mandate 레이어만 통과하면 그대로 제출된다. 같은
테스트 파일의 주석(208-214행)은 이 동작을 "의도된 구분"으로 서술하지만, 그 의도는
"번들이 PAUSED인 경우까지 눈감아주지 않는다"는 점을 증명하는 데 그치고,
"mandate가 전혀 없는 기본 상태에서 CM-6/7이 완전히 꺼진다"는 1차 전제 자체는
검증하지 않는다.

대조: 같은 파일(`foundation_gate.py`)이 조립하는 숫자 mandate 레이어
(`foundation_gate_mandate_layer.py:41-78`)는 `mandate is None`이면 어떤 플래그와도
무관하게 `RISK_INPUT_MANDATE_MISSING`으로 즉시 DENY한다(선택적 매개변수조차 없음).
H-1b(task-3369)가 바로 이 비대칭(숫자 레이어는 `require_mandate=False` 잔존, 세
조립부 전부 교정)을 고친 전례가 있는데, CM-8 쪽의 동형 플래그는 같은 교정을 받지
않았다. `scripts/check_compliance_gate.py`는 "게이트가 호출되는가"와 "둘 다 id를
확인하는가"만 AST로 검사하므로 이 기본값 자체는 감지 대상이 아니다(검사 공백).

영향: CM-8 docstring(1-21행)이 명시하는 "risk·숫자-mandate와 독립된 두 번째
권위"라는 설계 의도가, mandate 미설정이라는 가장 흔한 초기 상태에서 사실상
무력화된다 — restricted_list(금지 자산)뿐 아니라 향후 concentration/leverage/
liquidity/position_limit이 스냅샷 키 공백 없이 전부 연결되어도(§1에서 이미
추적 중인 별개 격차가 해소되어도) 이 플래그가 그대로면 똑같이 무력화된다.

제안 조치(정정 리프 범위, 이 리프에서는 수정하지 않음): 세 프로덕션 조립부에
`require_compliance_mandate=True`를 명시 전달하거나, 숫자 mandate 레이어와 동형으로
이 매개변수 자체를 없애 무조건 DENY로 바꾼다. `check_compliance_gate.py`에 "세
조립부가 `require_compliance_mandate=True`(또는 그 축을 제거한 무조건 DENY)인가"를
추가해 재발을 막는다.

### F2 — `policy_decision`/`mandate_revision`/`approval_requests`에 RLS 정책 부재 (M)

위치: `src/db/migrations/versions/b3c7f19ad2e6_rls_policies_foundation.py:42-55`의
`_FOUNDATION_TABLES`는 `portfolio_mandate`만 포함하고, 같은 서브모듈의
`mandate_revision`/`policy_bundle`/`policy_decision`은 "부모 FK로 간접 격리되므로
제외"라는 10-16행의 설계 근거로 빠져 있다. 그러나 `policy_decision`은 그 근거가
들어맞지 않는다 — `src/foundation/mandates/adapters/postgres_policy_repository.py:
88-122`의 `insert_policy_decision`/`get_cached_decision`은 `mandate_revision_id`를
거쳐 조인하는 게 아니라 `policy_decision.tenant_id` 컬럼을 직접 매개변수로 받아
`self._pool.acquire()`(tenant_transaction 아님)로 조회·삽입한다 — task-9420/F1이
`portfolio_mandate`에서 고친 것과 정확히 같은 유형("tenant_id 컬럼을 직접 갖고 WHERE
절에만 의존", F1 선례·계약 대조 표 3행)의 테이블이 RLS 바인딩 이전에, RLS *정책
자체가* 없다. `c9f4e2a1b6d7_fa2c_force_rls_foundation.py:36`도 `portfolio_mandate`만
FORCE하고 나머지는 다루지 않는다.

`src/db/migrations/versions/189bdd25a017_approval_requests.py:29-46`의
`approval_requests`는 `tenant_id` 컬럼조차 없고(`user_id`만, PLATFORM scope는
NULL), `b3c7f19ad2e6`의 8개 foundation 목록·legacy 3개 목록 어디에도 들지 않는다 —
PLT-30 M5가 "foundation 서브모듈 최상위 테이블"만 범위로 잡았으므로(해당 마이그레이션
10-19행) `src/core/approval`은 원래 그 스코프 밖이지만, 결과적으로 승인 요청이라는
민감 테이블이 RLS/정책 보호를 전혀 받지 않는 상태로 남아 있다.

재현: 현재 `DATABASE_URL` 앱 역할이 superuser(`rolbypassrls=true`)이므로 오늘
당장 교차 테넌트 조회가 성립하지는 않는다(task-9420/F1 당시 재현에 쓴 "명시적
`SET LOCAL ROLE aios_app`" 재현도 여기서는 RLS 정책이 없으므로 같은 방식으로 재현할
수 없다 — 정책이 없으니 ROLE을 바꿔도 전체가 보인다. 이 재현은 별도로 하지 않았다,
'미검증'). 해당 역할이 언젠가 superuser 권한을 내려놓는 순간(task-9420/F1이 이미
예견한 "그 날") `policy_decision`/`mandate_revision`/`approval_requests`는 RLS로도
막히지 않는다 — WHERE절 하나를 빠뜨린 쿼리 하나가 바로 교차 테넌트 노출이 된다.

제안 조치: `b3c7f19ad2e6`/`c9f4e2a1b6d7`와 동형의 후속 마이그레이션으로
`policy_decision`(tenant_id 직접 소유)에 RLS 정책+FORCE를 추가하고,
`insert_policy_decision`/`get_cached_decision`을 `tenant_transaction`으로 전환한다.
`approval_requests`는 스코프 결정이 필요하다 — `user_id`를 tenant_id로 간주할 수
있는지(개인 tenant 불변조건), 혹은 이 테이블을 PLT-30 M5 범위에 공식 편입할지는
PM/CTO 결정 사항으로 남긴다.

### F3 — `propose_amendment`의 revision_no 동시 할당 경합 (L)

위치: `src/foundation/mandates/application/propose_amendment.py:65-66`
(`next_revision_no = max(r.revision_no for r in existing_revisions) + 1` — 조회와
삽입 사이 잠금 없음), `src/foundation/mandates/adapters/postgres_repository.py:
150-172`의 `insert_draft_revision`은 `UniqueViolationError`를 잡지 않는다(같은
파일 104-124행의 `get_or_create_mandate`는 동일 패턴의 경합을 명시적으로 잡아
승자의 행을 되읽는다 — 이 파일 안에서도 비대칭).
`d8e8e4ba2365_portfolio_mandate_policy.py:75`의 `UNIQUE (mandate_id, revision_no)`
제약이 있어 두 번째 삽입은 반드시 실패한다(데이터 손상 없음, fail-closed 유지).

재현: 미검증(실제 DB 동시성 테스트 미실행) — 정적으로는, 같은 `mandate_id`에 대해
`propose_amendment`를 거의 동시에 두 번 호출하면 두 호출 모두
`existing_revisions`를 같은 상태에서 읽어 같은 `next_revision_no`를 계산하고, 늦게
INSERT하는 쪽이 `asyncpg.UniqueViolationError`를 그대로 호출부(`src/api/routers/
foundation/mandates.py:89`)까지 전파한다 — 라우터에 이 예외 전용 처리가 없으면
도메인 예외(`ApprovalError` 류)가 아니라 원시 DB 예외가 500으로 노출된다.

제안 조치: `get_or_create_mandate`와 동형으로 `insert_draft_revision`에서
`UniqueViolationError`를 잡아 "DRAFT/PROPOSED가 이미 진행 중"(또는 재시도 유도)
도메인 예외로 변환하거나, `activate_revision`이 이미 쓰는 advisory-lock/CAS 패턴을
`propose_amendment`의 revision_no 할당에도 적용한다.

## 3. 미검증 표

| 항목 | 상태 |
|---|---|
| F1 — mandate 미설정 테넌트의 CM-8 패스스루 | 검증됨(기존 통합테스트 `test_bypass_attempts.py:238-249` 실행 결과로 확인, 재실행하지 않고 코드·테스트 내용 대조만 함) |
| F2 — `policy_decision` RLS 부재의 실제 교차 테넌트 노출 | 미검증(현재 role이 superuser라 재현 불가, 정적 증거만) |
| F3 — `propose_amendment` 동시 호출 레이스 | 미검증(동시성 테스트 미실행, 제약·코드 경로 정적 분석만) |
| CM-9/CM-10 일별 배치(`run_daily_post_trade_batch`)의 advisory-lock 직렬화 | 선례와 일치 확인, 새 발견 없음 — 실제 동시 2-인스턴스 재현은 미검증 |
| approval 서비스 장애(DB 커넥션 끊김 등) 시 대기 주문 처리 | 코드상 예외가 그대로 전파되어 승인되지 않은 상태로 남는다(fail-closed) — 실제 장애 주입 재현은 미검증 |

## 4. 결론

발견 3건(S1/M1/L1)으로 §B -2의 "같은 도메인 2회 연속 발견 0건" 조건은 이번 회차로
충족되지 않는다 — 후속 PM 사이클에서 F1~F3 각각의 정정 리프를 발행한 뒤 재감사가
필요하다. 이 리프는 감사 전용이며 코드 변경을 포함하지 않는다.
