# 레드팀 점검 기록

이 문서는 DevEngine 세션(별도 Claude Code 세션, `C:\devengine\mihwa-devengine`
작업 중)이 `C:\aios\mihwa-aios`를 읽기 전용으로 감사하며 찾은 문제를
기록한다 — **이 세션은 AIOS 코드를 직접 수정하지 않는다**, AIOS 구현을
맡은 세션이 이 문서를 보고 판단해서 고치는 용도다.

각 항목은 발견 시점의 실제 코드/테스트 근거를 남기고, 상태(OPEN/FIXED)를
갱신한다. FIXED로 표시된 항목은 감사 세션이 수정 커밋까지 직접 확인한
것이다(자체 보고 아님 — ruff/mypy/pytest 실행 결과로 검증).

---

## 2026-08-29-01 · get_current_user()가 계정 정지 상태를 요청마다 확인하지 않음

**상태**: ✅ FIXED (커밋 `5c04f2d`, 감사 세션이 실행해 확인 — 525 tests passed)

**발견**: `src/api/deps.py::get_current_user()`가 JWT 서명·만료·사용자
존재 여부만 확인하고 `user.status`는 확인하지 않았다. `AuthService.authenticate()`는
로그인 시점에 SUSPENDED/DELETED를 거부하지만, 이미 발급된 JWT는 그
자체로 만료 전까지(기본 `JWT_EXPIRE_MINUTES=60`) 유효해서 — 운영자가
`PATCH /admin/users/{id}/status`로 계정을 정지시켜도 그 사용자가 이미
갖고 있던 토큰으로는 최대 60분간 API를 계속 쓸 수 있었다(실행 시작,
포트폴리오 재조정, 출금 화이트리스트 등록 등 전부 포함).

**근거**: 당시 `tests/integration/test_admin_router.py::test_admin_can_list_and_change_user_status`는
상태 변경 자체만 검증하고, 정지된 사용자 본인의 토큰으로 재요청하는
시나리오는 522개 테스트 중 어디에도 없었음.

**수정 확인**: `deps.py:61-67`에 `if user.status in ("SUSPENDED", "DELETED"): raise 401`
추가, 새 테스트 `test_suspended_user_existing_token_is_rejected` 추가.
전체 스위트 525 passed.

---

## 2026-08-29-02 · CredentialResolver의 TTL 캐시가 실제로 동작하지 않음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 534 tests passed,
ruff/mypy clean). 감사 세션 재검증 대기. 커밋 해시는 이 leaf 커밋 메시지
참조.

**수정 내용**: 권장 방향 그대로 적용 — `main.py` lifespan이 앱 시작 시
`ExchangeCredentialService`+`CredentialResolver`를 한 번만 만들어
`app.state.credential_resolver`에 저장하고, `service_deps.py::
get_credential_resolver()`는 그걸 그대로 반환하도록 변경(더 이상 매
요청 새로 만들지 않음). `exchange_credentials.py`의
`register_credential`/`revoke_credential` 둘 다 성공 시
`resolver.invalidate(user_id, exchange)`를 호출하도록 배선.

**새 테스트**: `test_credential_resolver_is_a_real_singleton_across_requests`
(같은 앱 lifespan 안에서 두 번 호출해도 동일 인스턴스인지 직접 확인),
`test_register_and_revoke_invalidate_resolver_cache`(등록/해지 각각
`invalidate()` 호출 여부를 스파이 서브클래스로 확인, 2회 모두 호출됨을
검증).

**발견**: `src/services/credential_resolver.py`는 거래소 어댑터를 5분
TTL로 캐싱해 매 요청 재생성을 피하도록 설계됐고, 자격증명 해지/재등록
직후 캐시를 지우는 `invalidate()` 메서드까지 준비돼 있다. 그런데
`src/api/service_deps.py:69-73::get_credential_resolver`가 **매 HTTP
요청마다 `CredentialResolver(credential_service)`를 새로 생성**한다 —
FastAPI `Depends()`는 요청 하나 안에서만 인스턴스를 재사용하고 요청
간에는 재사용하지 않으므로, 인스턴스 내부의 `_cache` dict가 매번 빈
채로 시작한다.

**결과**:
1. 설계된 5분 캐시가 한 번도 실제로 작동한 적이 없다 — 매 요청 어댑터를
   처음부터 다시 만든다(성능 최적화가 통째로 죽어있음).
2. `invalidate()`는 어디서도 호출되지 않는다(자기 단위테스트
   `tests/integration/test_credential_resolver.py` 말고는 참조 0건) —
   `revoke_credential`/`register_credential` 라우터(`src/api/routers/exchange_credentials.py`)
   둘 다 호출 안 함.
3. **역설적으로 캐시가 원래부터 죽어있어서** "해지 후에도 옛날
   자격증명이 캐시로 몇 분간 계속 쓰인다"는 실제 보안 위험은 지금은
   발생하지 않는다(의도치 않게 안전). 다만 캐싱을 나중에 진짜
   싱글턴으로 고치면 그 순간부터 `invalidate()` 미배선이 실제 보안
   구멍으로 살아난다 — 캐싱 수정과 `invalidate()` 배선은 반드시 같이
   가야 한다.

**권장 수정 방향**: `CredentialResolver`를 `request.app.state`(다른
싱글턴들 — `pool`, `event_bus`와 동일한 패턴)에 앱 시작 시 한 번만
생성해서 저장하고, `get_credential_resolver`는 그걸 반환하도록 변경.
`revoke_credential`/`register_credential` 라우터가 각각
`resolver.invalidate(user_id, exchange)`를 호출하도록 배선.

---

## 2026-08-29-03 · 자본 배분 상한이 실행(execution) 단위로만 검증되고 누적 배분은 검증하지 않음 — 확인 필요(스펙 의도 불명)

**상태**: ⚪ 확인 완료 — 버그 아님, 의도된 설계로 판단(구현 세션)

**판단 근거**: `config/risk_policy.yaml`을 직접 읽어보니
`strategy_allocation`(10%/25%, `capital_allocation.py`가 검사하는 대상)과
`position_concentration.single_asset_max_pct`(20%), `correlation_risk.
aggregate_exposure_max_pct`(30%)가 **완전히 별개 섹션**으로 이미
설계돼 있다 — 후자 둘이 정확히 이 항목이 우려한 "포트폴리오 전체 누적
노출"에 대응하는 자리다. `src/core/loader/risk_policy_loader.py`가 이
값들을 `RiskPolicy` 모델에 로드는 해두지만(109/112행)
실제로 이 값을 소비하는 코드는 `grep` 결과 아무 데도 없다 — FD-8(실제
주문 실행 엔진, FROZEN Zone)이 주문 시점에 강제할 몫으로 이미 자리만
마련해둔 것이고, 그 엔진 자체가 이 세션 스콥에 없어 아직 아무도
호출하지 않는 상태(이 세션 내내 반복된 "판단 로직은 있지만 구동시키는
호출부가 없다" 패턴과 동일). 즉 FD-16.1의 10%/25% 검사는 원문 그대로
"전략 하나에 대한 개별 집중도 제한"이 맞고, "총 배분이 잔고를 못
넘게 하라"는 별도 요구사항은 다른 정책 섹션이 이미 맡고 있어
`capital_allocation.py`가 중복으로 처리할 필요가 없다. 조치 없음.

**발견**: `src/services/capital_allocation.py::validate_capital_allocation()`은
"이번에 새로 만드는 실행 하나"의 `allocated_capital`이 거래소의 **현재
원시 잔고**(`available_balance`, `CredentialResolver`로 실시간 조회) 대비
certified_badge에 따른 상한(미인증 10%/인증 25%)을 넘는지만 검사한다.
같은 사용자가 이미 만들어둔 다른 RUNNING/PAUSED 실행들의
`allocated_capital` 합계는 전혀 고려하지 않는다.

**재현 가능한 시나리오**: 미인증 전략 A에 잔고의 10%를 배분해 실행을
만들고, 곧바로 미인증 전략 B에도 (아직 잔고가 실제로 안 빠져나갔으니)
또 10%를 배분해 실행을 만들 수 있다 — 순차 호출이라도(동시 요청 경쟁
없이도) 이론상 반복해서 잔고의 100%를 훌쩍 넘는 총 배분이 DB상으로는
전부 "정상 생성"으로 기록될 수 있다. 실제 자금은 LIVE 모드+실주문
시점에야 거래소가 최종적으로 거부하겠지만, PAPER 모드에서는 이 실행들이
그대로 굴러가며 포트폴리오 조회(19번, `PortfolioService`)의 비중 계산이
왜곡될 수 있다.

**확인이 필요한 이유**: 이게 "버그"인지는 FD-16.1 원문의 의도에 달려
있다 — ①"단일 전략에 몰빵하지 말라"는 개별 리스크 분산 규칙만 의도한
것이라면 지금 구현이 맞고, ②"총 배분이 잔고를 넘지 못하게 하라"는
포트폴리오 전체 상한까지 의도한 것이라면 실제로 빠진 검증이다.
`ExecutionService.create_execution()`이 `available_balance`를 계산할 때
기존 실행들의 누적 배분을 빼는 로직이 전혀 없다는 것만은 코드로 확실히
확인됨(`src/api/routers/executions.py::_available_balance`가 거래소
원시 잔고를 그대로 반환).

---

## 2026-08-29-04 · ApprovalService.approve()에 원자적 잠금이 없어 이중승인/DUAL 서명 위조가 가능 — 심각도 높음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 534 tests passed,
ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — SOLO 승인/첫서명/둘째서명/reject() 4개
UPDATE 모두 `WHERE id = $1 AND status = 'PENDING'`(첫서명은
`first_approver_id IS NULL`, 둘째서명은 그에 더해 `first_approver_id !=
$approver_id`) 조건을 걸고 `RETURNING`이 빈 행이면 `ApprovalError`를
던지도록 `approve()`/`reject()`를 다시 썼다 — `_update()` 제네릭
헬퍼 대신 각 전이별 전용 원자적 UPDATE로 교체(cancel()/expire_pending()은
이번 항목 범위 밖이라 그대로 둠, expire_pending()은 원래부터 조건부
UPDATE였음).

**새 테스트**: `test_concurrent_solo_approvals_only_one_succeeds`,
`test_concurrent_dual_first_signature_only_one_succeeds`(단순
asyncio.gather로는 두 _fetch()가 실제로 겹친다는 보장이 없어 — 커넥션
풀 라운드트립이 우연히 어긋나면 레이스 자체가 재현 안 될 수 있음을
직접 확인함 — barrier로 두 호출의 첫 조회가 반드시 동시에 끝나도록
강제해 원래 보고된 레이스 조건을 결정적으로 재현). 둘 다 5회 반복
실행해 재현성 확인.

**발견**: `src/core/approval/service.py::approve()`가 "① `_fetch`로
현재 상태 읽기 → ② status/시간 검사 → ③ `_update`로 별도 UPDATE 실행"
3단계를 잠금 없이 순차 실행한다. `_update()`의 실제 SQL은
`UPDATE approval_requests SET ... WHERE id = $1`(206-216행) — **`status`
조건이 SET절에도 WHERE절에도 없다.** DevEngine 세션 자신의
`approve_task`(같은 프로젝트 계열의 다른 서비스)가 정확히 이 문제를
막으려고 `SELECT ... FOR UPDATE`로 재확인 후에만 되돌릴 수 없는 동작을
실행하는 것과 대조적이다.

**재현 시나리오 1 — SOLO 모드 이중승인**: 같은 `request_id`에 대해
`approve()`가 거의 동시에 두 번 호출되면(같은 사용자의 중복 클릭,
클라이언트 재시도, 또는 두 명이 동시에 승인 버튼을 누르는 경우) 둘 다
`request.status != "PENDING"` 검사를 통과한 뒤(어느 쪽 UPDATE도 아직
커밋 전이므로) 둘 다 `_update(..., status="APPROVED", ...)`를 실행한다
— 호출부(예: LIVE 실행 시작, 비상출금 승인)가 `approve()`의 반환값을
보고 실제 동작을 트리거한다면, **되돌릴 수 없는 동작이 두 번 실행될 수
있다.**

**재현 시나리오 2 — DUAL 모드 서명 위조(더 심각)**: DUAL 모드는
"서로 다른 두 계정이 순차로 서명"해야 하는 게 핵심 안전장치(4.9 원칙,
docstring에 명시)인데, `first_approver_id is None`일 때의 분기(165-166행)에
아무 조건부 UPDATE가 없다. 서로 다른 두 사용자 A, B가 `first_approver_id`가
아직 비어있는 상태에서 거의 동시에 `approve()`를 호출하면 **둘 다**
"내가 첫 서명자다" 분기를 타고, 나중에 커밋되는 쪽이 앞선 쪽의
`first_approver_id`를 조용히 덮어쓴다 — 실제로는 한 명만 서명한 순간에
시스템 기록상 그 사람이 첫 서명자가 될 수도, 안 될 수도 있는 등 DUAL
모드가 보장해야 할 "독립된 두 사람의 서명"이라는 전제 자체가 타이밍에
따라 깨질 수 있다. 악의적 조작이 아니라 순수 동시 요청 타이밍만으로도
발생한다.

**권장 수정 방향**: `_update()`의 SQL에 낙관적 동시성 체크를 건다 —
예: `status="APPROVED"`로 가는 UPDATE는
`WHERE id = $1 AND status = 'PENDING'`으로, DUAL의 첫 서명 UPDATE는
`WHERE id = $1 AND first_approver_id IS NULL`로 조건을 걸고, `RETURNING *`이
빈 행을 반환하면(=이미 누군가 먼저 바꿨다는 뜻) `ApprovalError`를
던지도록 `approve()`를 고친다. 이렇게 하면 별도 트랜잭션/명시적 락 없이
단일 원자적 UPDATE만으로 이 프로젝트의 다른 곳(`check_budget_before_step`의
`FOR UPDATE` 패턴)과 동등한 안전성을 확보할 수 있다.

---

## 2026-08-29-05 · "읽고 → 별도로 쓰기" 상태전이 패턴이 이 코드베이스에 산발적으로 반복됨 — 04번 항목과 같은 근본원인, 다른 파일들

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 534 tests passed,
ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — `verification_service.py::decide()`의
두 UPDATE(APPROVE/REJECT)에 `AND status = 'PENDING_VERIFICATION'`,
`dispute_resolution_service.py::resolve()`의 UPDATE에
`AND status = 'OPEN'`을 추가하고, 각각 `RETURNING`이 빈 행이면
`VerificationError`/`DisputeResolutionError`를 던지도록 수정 —
`payment_confirmation_service.py::confirm_payment()`와 완전히 동일한
패턴. 전수조사 권고대로 다른 상태-전이 UPDATE도 훑어봤으나(marketplace/
admin/execution 계열) 이 두 곳 외에 같은 패턴은 추가로 없었다.

**새 테스트**: `test_concurrent_decisions_only_one_succeeds`(서로 다른
두 검증담당자가 같은 리스팅을 승인/반려로 동시 처리),
`test_concurrent_resolutions_only_one_succeeds`(서로 다른 두 관리자가
같은 분쟁을 서로 다른 결정으로 동시 처리) — 둘 다 asyncio.gather로
5회 반복 실행해 재현성 확인(04번과 달리 이 두 경로는 "성공 결과가
경로별로 다르지 않은" 구조라 barrier 없이도 안정적으로 재현됨).

**발견**: 04번(`ApprovalService.approve()`)을 찾고 나서, 같은 모양의
코드(먼저 SELECT로 status를 Python에서 확인 → 그 다음 별도 UPDATE를
`WHERE id = $1`만으로 실행, status 조건 없음)가 이 코드베이스에 더
있는지 훑어봤다. 아래 두 곳에서 동일 패턴을 확인:

- `src/services/verification_service.py::decide()` (66-87행) — 리스팅이
  `PENDING_VERIFICATION` 상태인지 Python에서 확인한 뒤, UPDATE는
  `WHERE id = $1`뿐. 서로 다른 두 검증담당자가 같은 리스팅을 거의 동시에
  하나는 승인, 하나는 반려하면 나중에 커밋되는 쪽이 최종 상태를
  결정하고, `_publish()`가 그 경쟁에서 어느 쪽이 이겼는지와 무관하게
  `new_status`(자신이 방금 계산한 값)를 그대로 발행해 실제 최종 DB
  상태와 발행된 이벤트가 어긋날 수 있다.
- `src/services/dispute_resolution_service.py::resolve()` (89-112행) —
  `detail.status != "OPEN"`을 Python에서 확인한 뒤, `conn.transaction()`
  안에서도 UPDATE가 `WHERE id = $1`뿐(status 조건 없음). **`conn.transaction()`이
  있다고 이 레이스가 막히는 게 아니라는 점이 중요** — 트랜잭션은 그
  안의 여러 문장을 원자적으로 묶어줄 뿐, 기본 격리수준(READ COMMITTED)에서는
  다른 트랜잭션이 커밋 전 상태를 읽고 동시에 진행하는 것 자체를 막지
  않는다. 두 관리자가 같은 분쟁을 거의 동시에 서로 다른 결정으로 처리하면
  하나가 조용히 덮어써진다.

**이미 올바르게 구현된 대조군이 같은 저장소에 있음**:
`src/services/payment_confirmation_service.py::confirm_payment()`(100-127행)는
`conn.transaction()` + `UPDATE strategy_purchases SET payment_status = 'CONFIRMED', ... WHERE id = $1 AND payment_status = 'PENDING_PAYMENT'`처럼
**상태 조건을 UPDATE 자체에 건다** — 이게 정확히 필요한 패턴이다. 04/05번
항목 모두 이 파일이 이미 쓰고 있는 방식을 그대로 옮기면 해결된다.
`purchase_service.py::purchase()`는 확인해봤는데 리스팅 상태를 변경하지
않는 구조(한 리스팅을 여러 구매자가 동시에 사도 되는 게 의도된 동작)라
해당 없음. `seller_suspension_service.py::suspend()`는 멱등한 boolean
플래그 설정이라 경쟁이 생겨도 결과가 같아 해당 없음.

**권장 수정 방향**: `verification_service.py`의 두 UPDATE에
`AND status = 'PENDING_VERIFICATION'`을, `dispute_resolution_service.py`의
UPDATE에 `AND status = 'OPEN'`을 추가하고, `RETURNING`이 빈 결과면 각각의
Error를 던지도록 수정 — `payment_confirmation_service.py`와 완전히 같은
패턴. 이 기회에 상태 컬럼이 있는 다른 UPDATE도 같은 기준으로 한 번 더
훑어보는 걸 권장(이 감사는 전수조사가 아니라 발견된 것만 확인한 것).

---
