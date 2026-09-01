# 레드팀 점검 기록

이 문서는 DevEngine 세션(별도 Claude Code 세션, `C:\devengine\mihwa-devengine`
작업 중)이 `C:\aios\mihwa-aios`를 읽기 전용으로 감사하며 찾은 문제를
기록한다 — **이 세션은 AIOS 코드를 직접 수정하지 않는다**, AIOS 구현을
맡은 세션이 이 문서를 보고 판단해서 고치는 용도다.

각 항목은 발견 시점의 실제 코드/테스트 근거를 남기고, 상태(OPEN/FIXED)를
갱신한다. FIXED로 표시된 항목은 감사 세션이 수정 커밋까지 직접 확인한
것이다(자체 보고 아님 — ruff/mypy/pytest 실행 결과로 검증).

---

## 2026-09-01-08 · PAPER 실행 루프가 adapter의 실제 sandbox 상태를 증명하지 않음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 통합테스트 25개
통과, ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향의 축소판 — 전체 `PaperExecutionAdapter`/
`ExecutionEnvironment` attestation 체계(별도 ADR·owner review 대상으로
명시된 큰 리팩터링)까지는 가지 않고, `ExchangeAdapter`에 `is_sandboxed:
bool` 추상 프로퍼티를 신설해 최소한의 실효성 있는 방어를 지금 바로
넣었다. `BitgetAdapter.is_sandboxed`는 생성자의 `demo_mode`를,
`KISAdapter.is_sandboxed`는 `is_paper_trading`을 그대로 노출한다(둘 다
paptrading 헤더/모의투자 base URL 전환과 동일 조건 — 새 상태를 만들지
않고 이미 있던 진실의 원천을 노출만 함). `Executor.execute()`는 이제
`mode == 'PAPER'`뿐 아니라 `adapter.is_sandboxed`도 함께 확인해, 둘 중
하나라도 아니면 `FrozenZoneLiveModeBlockedError`를 던진다 — DB의 mode
문자열과 실제 adapter 상태가 반드시 둘 다 일치해야 통과한다.

**새 테스트**:
`test_paper_mode_with_non_sandboxed_adapter_is_hard_blocked`
(`tests/integration/test_executor.py`) — `mode='PAPER'`인데
`is_sandboxed=False`인 adapter를 넘기면 주문이 전혀 나가지 않고
차단되는지 확인.

**남은 축소(정직하게 명시)**: 이 수정은 "adapter 객체 스스로가 보고하는
값"을 신뢰한다 — 이 세션이 만든 `CredentialResolver`가 여전히 유일한
adapter 생성 경로(`main.py`에서 `demo_mode` override 없이 기본값 True로
생성)라 지금은 안전하지만, egress allowlist·credential provenance 검증까지
포함한 완전한 attestation 체계는 이 리프의 스콥이 아니다 — 권장사항이
명시한 대로 실계정 도입 시점에 별도 ADR·owner review로 분리해야 한다.

**발견 당시 배경**: production router나 scheduler가
`run_execution_tick()`을 호출하는 경로는 확인되지 않았고, 통합 테스트는
`FakeExchangeAdapter`만 사용했다. 그러나 이 라이브러리 경계가 이후 배선될
때 안전한 paper-only 보장을 제공하지 못하므로, production wiring 전에
해소가 필요하다고 판단됐다.

**발견**: `src/core/executor/executor.py::Executor.execute()`는
`mode != "PAPER"`을 차단하지만, 전달받은 `ExchangeAdapter`의 실제 계정·endpoint·demo
상태는 확인하지 않은 채 `src/services/order_service/submit.py::submit_order()`가
`adapter.place_order(order)`를 호출한다. `ExchangeAdapter` interface에는
"현재 sandbox/paper account에 바인딩됨"을 증명하는 속성/attestation이 없다.
실제 구현도 `BitgetAdapter(demo_mode=False)` 또는
`KISAdapter(is_paper_trading=False)`로 생성 가능하다. 따라서 단지 DB execution
mode가 `PAPER`라는 사실만으로, 잘못 구성된 real adapter를 통한 외부 주문을
코드 수준에서 막지는 못한다.

**영향**: 현재에는 호출 배선이 없는 library code이므로 즉시 외부 주문이
노출됐다는 증거는 없다. 다만 향후 scheduler/worker가 `run_execution_tick()`에
실제 adapter를 주입하면 configuration error 하나가 paper 실행을 real endpoint로
보낼 수 있다. 이는 `PAPER 모드는 hard guard`라는 Executor docstring의 보장보다
약하며, 플랫폼 기준 문서가 요구하는 account/credential/provider adapter 수준의
paper/live 분리와도 맞지 않는다.

**권장 수정 방향**: execution plane에 별도 `PaperExecutionAdapter` 또는
검증 가능한 immutable `ExecutionEnvironment` attestation을 도입한다. paper
worker는 sandbox endpoint, sandbox credential provenance, egress allowlist를
확인한 adapter만 얻을 수 있어야 하며, `Executor.execute()`/tick entrypoint는
attestation이 없거나 LIVE인 adapter를 fail-closed로 거부해야 한다. 이 경계에는
`PAPER + live-configured adapter` negative test와 실제 network egress를 막는
integration test를 추가한다. 기존 LIVE path를 수정/활성화하는 작업은 별도
ADR·owner review·release approval로 분리한다.

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

## 2026-08-29-06 · WatchdogSnapshot.exchange_healthy가 계산만 되고 decide() 판정에는 반영되지 않음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 549 tests passed,
ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 (b) 채택 — `exchange_healthy`를 `decide()`에
새로 넘기지 않고, 대신 API 중복호출만 없앴다. `watchdog_process.py`에
`_LatestExchangeHealth` 캐시 클래스를 추가해 `run_one_cycle()` 맨 앞에서
`check_exchange()`를 사이클당 정확히 한 번만 실제로 호출하고, 그 결과를
`WatchdogService`(생성자에 `health_check=exchange_health_cache.get`으로
바인딩, 스냅샷의 `exchange_healthy` 필드는 관측용으로 유지)와
`split_brain.diagnose(check_exchange=exchange_health_cache.get, ...)`
양쪽이 캐시에서 재사용하도록 배선했다 — 필드 자체를 제거하지 않은 이유는
사람이 로그(`snapshot`)로 확인할 수 있는 관측치로서 가치가 있고, 판정
경로는 이미 Split-Brain(9.3)이 원문대로 전담하고 있어 `decide()`에 새
파라미터를 추가하는 (a)안은 8.2-A 원칙("판정 로직 변경은 최소")에 비해
불필요한 표면적 확장이라고 판단했기 때문이다.

**새 테스트**: `test_run_one_cycle_calls_check_exchange_exactly_once`
(`check_exchange` 콜백에 호출 카운터를 심어 `run_one_cycle()` 1회 실행 시
정확히 1회만 호출됐는지 직접 확인).

**발견**: `src/core/safety/watchdog.py::decide()`(137-163행)는
`snapshot.unresponsive_sec`와 `snapshot.loss_pct`만 보고 판정한다 —
`WatchdogSnapshot.exchange_healthy`(44행, `WatchdogService.take_snapshot()`이
매 사이클 실제로 계산해 채워넣는 필드)는 `decide()` 시그니처에 아예
들어가지 않아 HALT/LIQUIDATE/NORMAL 어느 쪽 판정에도 영향을 주지 않는다.

**참고**: 안전 기능 자체가 완전히 빠진 것은 아니다 — `watchdog_process.py`의
`run_one_cycle()`(107-140행)을 보면 거래소 헬스체크(`check_exchange`)가
Split-Brain 진단(`split_brain.diagnose(check_exchange=...)`, 9.3)
쪽에는 별도로 다시 전달돼 그쪽 판정에는 실제로 반영되고 있다. 다만:
1. `check_exchange()`가 같은 사이클에 **두 번** 호출된다 — 한 번은
   `WatchdogService.take_snapshot()` 내부의 `health_check()`로(그
   결과가 `exchange_healthy`에 저장되지만 이후 어디서도 안 읽힘), 한
   번은 `split_brain.diagnose(check_exchange=check_exchange, ...)`로.
   Bitget 공개 API를 폴링 주기(Draft 5초)마다 불필요하게 2배로 호출.
2. `WatchdogSnapshot.exchange_healthy` 필드 자체가 죽은 코드라, 나중에
   이 필드를 보고 "판정에 반영되고 있겠지"라고 오해하기 쉽다 —
   FD-9.1 원문("거래소 API 자체 헬스체크를 독립적으로 감시한다")이
   `decide()` 자체의 판정 근거를 의도한 것인지, 지금처럼 Split-Brain
   경로로만 처리해도 되는 것인지는 스펙 의도 확인이 필요.

**권장 수정 방향**: (a) `exchange_healthy`를 실제로 `decide()`에 넘겨
판정에 반영하거나, (b) 지금처럼 Split-Brain 경로가 전담하는 게
의도라면 `WatchdogSnapshot`에서 이 필드를 빼고 `health_check`를
`WatchdogService` 생성자에서 제거해 중복 호출과 죽은 필드를 둘 다
없애는 쪽이 더 깔끔해 보임. 어느 쪽이든 `check_exchange()` 결과를
사이클당 한 번만 계산해 `take_snapshot()`과 `split_brain.diagnose()`
양쪽에 공유하도록 하면 중복호출 문제는 같이 해결됨.

---

## 2026-08-29-07 · heartbeat.write_heartbeat()가 원자적 쓰기가 아님 — 이론상 거짓 HALT 유발 가능

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 549 tests passed,
ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — `write_heartbeat()`가 대상 경로에 직접
쓰지 않고, 같은 디렉터리의 `.tmp` 파일에 먼저 쓴 뒤 `os.replace(tmp_path,
path)`로 교체하도록 변경했다. `os.replace`는 POSIX와 Windows NTFS 둘 다
원자적이라 읽는 쪽(`read_heartbeat_age_seconds`)은 항상 "이전 값 전체"
또는 "새 값 전체" 중 하나만 관측한다 — truncate~쓰기 사이 찰나가 아예
존재하지 않게 되므로 재현 시나리오 자체가 성립하지 않는다.
`decide()` 자체에 히스테리시스가 없다는 부수 관찰은 이번 수정 범위
밖으로 남겨둔다(원자적 쓰기로 이 항목이 지적한 근본 원인은 제거됐고,
히스테리시스 도입 여부는 스펙 의도 확인이 별도로 필요한 사안이라 이
leaf에 묶지 않음).

**새 테스트**: `test_write_heartbeat_leaves_no_temp_file_and_content_is_valid`
(쓰기 후 `.tmp` 파일이 남지 않고, 최종 파일 내용이 유효한 timestamp로
파싱되는지 확인 — 원자성 자체는 OS 레벨 보장이라 경쟁조건을 직접
재현하지는 않음, `os.replace`의 원자성은 표준 라이브러리 계약으로 신뢰).

**발견**: `src/core/safety/heartbeat.py::write_heartbeat()`(21-24행)가
`path.write_text(str(time.time()), encoding="utf-8")`로 파일에 직접
쓴다 — 임시파일에 쓴 뒤 `os.replace()`로 교체하는 원자적 패턴이
아니다. 같은 파일을 읽는 `read_heartbeat_age_seconds()`(27-36행)는
"파일이 없거나 손상된 경우 무한대로 간주"하는 fail-closed 원칙을
이미 갖고 있다(빈 문자열은 `float()` 변환 실패 → `ValueError` →
`float("inf")` 반환).

**재현 시나리오(이론상)**: 메인 프로세스가 `write_heartbeat()`를
호출하는 도중(파일이 truncate된 직후, 새 값이 아직 다 안 써진 순간)
Watchdog 프로세스가 정확히 그 찰나에 같은 파일을 읽으면 빈 문자열
또는 잘린 숫자 문자열을 읽게 되고, `read_heartbeat_age_seconds`가
이를 "손상됨"으로 판단해 `float("inf")`(무응답)를 반환한다 —
`decide()`의 `unresponsive_sec_threshold`(기본 30초) 기준에서 이
찰나의 값은 다음 폴링 사이클(5초 후)이면 정상으로 돌아오므로
`_StreakTracker`류 히스테리시스가 없는 `decide()` 자체는 단발성
false HALT를 그대로 트리거할 수 있다(watchdog.py의 decide()에는
split_brain.py 같은 진입/복귀 히스테리시스가 없음 — 이것도 별개로
확인할 가치가 있어 보임).

**참고**: 실제 경쟁 구간은 마이크로초 단위(15바이트 남짓 쓰는 시간)라
발생 확률은 극히 낮다. 다만 오늘 함께 추가된 "Watchdog 오탐
시뮬레이터"(9.7, `watchdog_simulator.py`)는 임계값 기반 통계적
오탐(과거 시세 재생)만 다루고, 이런 IPC 메커니즘 자체의 경쟁조건은
시뮬레이터 범위 밖이라 지금 테스트 커버리지가 없다.

**권장 수정 방향**: `write_heartbeat()`를 임시파일에 쓴 뒤
`os.replace(tmp_path, path)`로 교체하는 원자적 패턴으로 변경(POSIX와
Windows NTFS 둘 다 `os.replace`는 원자적). 부수적으로, `decide()`
자체에 unresponsive 판정 히스테리시스가 없다는 점도 같이 확인해볼
가치가 있음(split_brain.py의 진입 3초/복귀 10초 비대칭 히스테리시스와
같은 원칙을 여기도 적용할지는 스펙 의도 확인 필요).

---

## 2026-08-29-08 · execution_service의 start()/pause()가 원자적 조건부 UPDATE가 아님 — Watchdog 강제정지(Kill Switch)를 사용자 요청이 덮어쓸 수 있음 — 심각도 높음, 감사 세션이 직접 재현조건까지 확인함

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 통합테스트 11개
통과, ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 04/05번과 동일 패턴 — `start()`의 UPDATE에 방금 읽은
`execution["status"]`를 `AND status = $2` 조건으로 추가(허용되는 시작
전 상태가 PENDING_APPROVAL/PAUSED 등 여러 값일 수 있어 리터럴이 아니라
변수 바인딩), `pause()`의 UPDATE에는 `AND status = 'RUNNING'`을
추가했다. 둘 다 `RETURNING`이 빈 행이면 `ExecutionControlError`(동시
처리 충돌)를 던진다 — Watchdog가 `status = 'RUNNING'` 조건으로 먼저
커밋하면 `start()`/`pause()`의 UPDATE 자체가 대상 행을 찾지 못해
자연히 실패한다(별도 SAFETY_LAYER 재확인 로직을 추가하지 않고도 조건부
UPDATE 하나로 막힘). `retire()`도 같은 계열 취약점이라 같이 하드닝
(`WHERE status IN ('RUNNING','PAUSED')` 추가).

**새 테스트**: `test_concurrent_safety_pause_blocks_racing_user_start`
(`tests/integration/test_execution_control.py`) — 04번 수정 때 확인한
대로 `asyncio.gather`만으로는 두 코루틴이 실제로 겹친다는 보장이 없어,
`start()`의 UPDATE 직전에 barrier를 걸어 그 사이 SAFETY_LAYER `pause()`가
반드시 먼저 커밋되도록 강제해 원래 시나리오를 결정적으로 재현했다(수정
전 코드로 되돌려 이 테스트가 실제로 실패하는 것까지 직접 확인).

**발견**: `src/services/execution_service.py::start()`(170-217행)와
`pause()`(219-257행) 둘 다 상태를 Python에서 SELECT로 읽어 검사한 뒤,
최종 UPDATE는 `WHERE id = $1`만 걸려 있고 방금 읽은 상태를 조건으로
다시 확인하지 않는다(`FOR UPDATE`도, `conn.transaction()`도 없음) —
04/05번 항목과 완전히 같은 근본원인.

**발견**: `src/services/execution_service.py::start()`(170-217행)와
`pause()`(219-257행) 둘 다 상태를 Python에서 SELECT로 읽어 검사한 뒤,
최종 UPDATE는 `WHERE id = $1`만 걸려 있고 방금 읽은 상태를 조건으로
다시 확인하지 않는다(`FOR UPDATE`도, `conn.transaction()`도 없음) —
04/05번 항목과 완전히 같은 근본원인.

**재현 시나리오 — 안전계층 Kill Switch 무력화**: 정책문서가 명시한
"8.6-B Kill Switch 우선순위"(사용자가 안전장치의 강제정지를 직접
재시작할 수 없어야 한다)를 실제로 깨뜨릴 수 있다.
1. 실행 X가 `RUNNING`.
2. `POST /executions/X/start`가 X를 SELECT — 아직 `status=RUNNING`,
   `paused_by=NULL`이라 "SAFETY_LAYER가 정지시킨 것" 검사(186-189행)를
   통과, 계속 진행.
3. 그 사이(별도 OS 프로세스인) `watchdog_process.py::_apply_decision()`이
   HALT/LIQUIDATE 판정으로 `UPDATE strategy_executions SET status='PAUSED',
   paused_by='SAFETY_LAYER' WHERE status='RUNNING'`을 먼저 커밋 — X가
   실제로 안전정지됨.
4. 2번의 `start()`가 이어서 실행하는 조건 없는 `UPDATE ... SET
   status='RUNNING', paused_by=NULL ... WHERE id=$1`이 그대로 커밋돼
   **방금 워치독이 건 안전정지를 조용히 덮어쓰고 실행을 재개시킨다.**
   `pause(paused_by='USER')`도 같은 구조라, 사용자의 일반 정지 호출이
   `SAFETY_LAYER` 정지와 경합하면 `paused_by` 값 자체가 덮어써질 수
   있고, 이후 `start()`의 SAFETY_LAYER 검사가 그 값에 의존하므로 연쇄적으로
   더 취약해진다.

이번 세션이 오늘 직접 감사한 Watchdog(2026-08-29-06/07번 항목)이
실제로 발동시키는 강제조치가 바로 이 `strategy_executions.paused_by`
갱신이라, 이 항목은 "워치독이 원래 하려던 일이 애초에 다른 경로로
무효화될 수 있다"는 점에서 06/07번보다 실제 영향이 더 크다.

**권장 수정 방향**: `payment_confirmation_service`/`verification_service`/
`dispute_resolution_service`가 04/05번 수정 때 이미 쓴 패턴 그대로 —
`start()`의 UPDATE에 `AND status = $2`(방금 읽은 값)를, `pause()`의
UPDATE에 `AND status = 'RUNNING'`을 추가하고 `RETURNING`이 빈 행이면
"동시 처리 충돌" 에러를 던지도록 수정. 특히 `start()`는 SAFETY_LAYER
정지 여부를 재확인하는 조건도 UPDATE 자체에 포함시켜야 한다(예:
`WHERE id=$1 AND NOT (status='PAUSED' AND paused_by='SAFETY_LAYER')`).

---

## 2026-08-29-09 · portfolio_service.rebalance()에 트랜잭션/잠금이 없어 동시 재조정 시 잔고 초과 배분이 가능 — 심각도 높음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 통합테스트 11개
통과, ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — `rebalance()` 전체를
`conn.transaction()`으로 감싸고, 조정 대상 실행 조회(`e.id = ANY($1)`)에
`FOR UPDATE OF e`, 전체 합계 재검증용 조회(`WHERE user_id = $1 AND
status IN (...)`)에 `FOR UPDATE`를 추가했다. 두 번째 동시 요청은 첫
번째 트랜잭션이 커밋할 때까지 자신의 `FOR UPDATE` 조회에서 실제로
블록되고, 커밋 후에는 최신(이미 반영된) 값으로 합계를 재검증하므로
"서로의 아직 커밋 안 된 변경을 못 본 채 각자 통과"하는 경합이 원천
차단된다. 부수적으로 트랜잭션 도입 자체가 "총합 초과 시 전체 원자적
거부(부분 반영 없음)"라는 기존 docstring의 약속도 실제로 보장하게 됐다
(이전에는 개별 UPDATE가 트랜잭션 없이 각각 즉시 커밋됐음).

**새 테스트**:
`test_concurrent_rebalance_does_not_allow_combined_total_to_exceed_balance`
(`tests/integration/test_portfolio_service.py`) — 미인증 전략 10% 배분
상한은 개별적으로 넘지 않으면서 두 실행의 합산만 잔고를 초과하도록
구성한 뒤, 첫 호출의 SELECT 직후에 실제 `asyncio.sleep`을 끼워 넣어
두 번째 호출이 그 사이 반드시 진입하도록 강제했다(잠금 자체는 인위적
지연이 아니라 실제 Postgres `FOR UPDATE`가 담당). 수정 전 코드로
되돌려 이 테스트가 실제로 실패(둘 다 성공해 합산이 잔고를 초과)하는
것까지 직접 확인.

**발견**: `src/services/portfolio_service.py::rebalance()`(145-240행)가
`conn.transaction()`도 `FOR UPDATE`도 없이, 잔고 초과 여부(`new_total
&gt; total_cash_balance`)를 일반 SELECT로 읽은 값만으로 검사한 뒤 각
실행의 `allocated_capital`을 순차적으로 UPDATE한다.

**재현 시나리오**: `total_cash_balance=250`, 실행 A/B 각각
`allocated_capital=50`(합 100, 여유 150). 동시에 두 요청 — 요청1
`rebalance([A→150])`, 요청2 `rebalance([B→150])` — 이 도착하면 둘 다
서로의 아직 커밋 안 된 변경을 못 본 채로 "150(자신) + 50(상대,
stale) = 200 ≤ 250"으로 각자 통과·커밋해버려, 최종 A=150+B=150=300이
실제 잔고 250을 초과한 채로 둘 다 "성공"으로 기록된다. docstring 자신이
약속한 "총합 초과 시 전체 원자적 거부(부분반영 없음)"도 트랜잭션이
없어 중간에 실패하면 깨질 수 있다.

**권장 수정 방향**: 전체를 `conn.transaction()`으로 감싸고, 재조정
대상 실행들을 `SELECT ... FOR UPDATE`로 잠그거나, 쓰기 직전에 같은
트랜잭션 안에서 잔고 제약을 다시 검증. 이 항목은 여러 행에 걸친
금액 불변식이라 `SERIALIZABLE` 격리수준도 고려할 만함.

---

## 2026-08-29-10 · SecretBundle.model_dump()/model_dump_json()이 __repr__ 마스킹을 우회해 모든 시크릿을 평문으로 반환함

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 테스트 15개
통과, ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — 실제 자격증명·키류 필드(database_url,
jwt_secret_key, credential_encryption_key, bitget_api_key/secret,
kis_app_key/secret, smtp_password, fcm_server_key, apns_key_id)를 전부
`pydantic.SecretStr`로 변경했다(jwt_algorithm/jwt_expire_minutes/
smtp_host/smtp_port/smtp_user/cors_allowed_origins는 자격증명 자체가
아니라 그대로 둠). `model_dump()`/`model_dump_json()`을 포함한 Pydantic의
모든 직렬화 경로가 이제 일관되게 마스킹된다. 이 필드들을 실제 값으로
소비하는 4개 호출부(`src/api/deps.py`, `src/api/service_deps.py`,
`src/main.py`, `src/watchdog_process.py`)는 전부 `.get_secret_value()`로
꺼내 쓰도록 갱신 — 이 4곳을 벗어나면 평문 문자열이 존재하지 않는다.
`secret_loader.py::load_env_secrets()`도 `.env` 원시 문자열을
`SecretStr(...)`로 감싸 전달하도록 갱신.

**새 테스트**: `test_load_env_secrets_model_dump_never_leaks_values`
(`tests/unit/core/loader/test_secret_loader.py`) — 감사가 재현한
시나리오 그대로 `model_dump()`/`model_dump_json()` 양쪽에서 원본 시크릿
값이 전혀 등장하지 않는지 직접 확인(기존 `repr()`만 확인하던 테스트로는
이 취약점 자체를 잡지 못했다는 점이 이번 감사의 핵심 지적이었음).

**발견**: `src/data/models/trading.py::SecretBundle`(107-136행)은
`__repr__`/`__str__`만 마스킹하도록 오버라이드했다("07번 §7.1 마스킹
원칙 — 어떤 필드도 평문 노출 금지"라고 자체 docstring에 명시). 직접
실행해 확인:

```
>>> b = SecretBundle(database_url=..., jwt_secret_key='SECRET123', ...)
>>> repr(b)
'SecretBundle(&lt;16 fields, masked&gt;)'
>>> b.model_dump()
{'database_url': '...', 'jwt_secret_key': 'SECRET123', 'credential_encryption_key': ...,
 'bitget_api_key': ..., 'bitget_api_secret': ..., 'kis_app_key': ..., 'kis_app_secret': ...,
 'smtp_password': ..., ...}  # 전부 평문
```

FastAPI는 라우트가 반환하는 Pydantic 객체를 `repr()`이 아니라
`model_dump_json()`으로 직렬화한다 — 지금 이 값은 `main.py`에서
`app.state.secrets`로 저장돼 있고(90행), 오늘 기준 이걸 그대로
반환/로깅하는 코드 경로는 없는 것으로 확인됐지만, 나중에 디버그용
엔드포인트나 "구조화 로깅"을 위해 무심코 `secrets.model_dump()`를
호출하는 코드가 추가되는 순간 DB 접속정보·JWT 서명키·거래소
API키·SMTP 비밀번호 전부가 그대로 노출된다 — 이 코드베이스가 다른
곳에서 계속 의존하고 있는 "SecretBundle은 절대 평문 노출 안 됨"이라는
전제 자체가 실제로는 성립하지 않는다.

**권장 수정 방향**: 각 시크릿 필드를 Pydantic v2의 `SecretStr` 타입으로
바꾸면(관용적인 방법) `model_dump()` 결과도 자동으로
`SecretStr('**********')`로 마스킹되면서 필요한 곳에서만
`.get_secret_value()`로 꺼내 쓸 수 있다. 또는 `model_dump`/
`model_dump_json` 자체를 오버라이드해도 되지만, Pydantic이 내부적으로
쓰는 다른 직렬화 경로(예: FastAPI의 `jsonable_encoder`)까지 전부
우회하지 않으려면 `SecretStr` 쪽이 더 안전함.

---

## 2026-08-29-11 · MfaService.verify()가 이미 활성화된 MFA를 아무 실패한 코드로든 영구 비활성화시킴 — 심각도 높음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 통합테스트
35개 통과, ruff/mypy strict clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — `MfaService.verify()`가 이제 SELECT
시점에 `mfa_enabled`도 함께 읽어, 이미 `true`(정상 활성화)인 상태의
검증 실패는 `MfaError`만 던지고 `mfa_secret`/`mfa_enabled` 행을 전혀
건드리지 않는다(secret 폐기는 `mfa_enabled=false`, 즉 최초 설정
검증대기 중일 때만 유지). 새 테스트
`test_verify_with_wrong_code_after_already_enabled_does_not_disable_mfa`
(`tests/integration/test_mfa_service.py`)가 이미 켜진 MFA는 틀린 코드
한 번으로 안 꺼지고 기존 secret으로 계속 검증 가능함을 실증.

별도로 권장됐던 `/mfa/setup` 재인증도 함께 적용 — `users.py`의
`_reauthenticate()`를 `src/api/deps.py::reauthenticate()`로 공용화해
재사용(중복 정의 대신 단일 지점), `MfaSetupRequest`(password/totp_code
선택 필드) 신설. `user.mfa_enabled=true`인 계정이 `/auth/mfa/setup`을
다시 호출하면 이제 비밀번호(+MFA 활성 상태이므로 TOTP도 함께) 재인증
없이는 403으로 거부되어 기존 secret을 덮어쓸 수 없다(최초 설정,
즉 `mfa_enabled=false`일 때는 로그인 자체가 이미 증명이라 재인증을
요구하지 않음 — 기존 동작 유지). 새 테스트
`test_mfa_resetup_without_password_rejected_when_already_enabled`/
`test_mfa_resetup_with_correct_password_succeeds`
(`tests/integration/test_auth_router.py`)가 왕복 검증.

**발견**: `src/services/mfa_service.py::verify()`(63-79행)는
`mfa_enabled`이 이미 `true`(정상 활성화 상태)인지 `false`(최초 설정
검증 대기 중)인지 구분하지 않고, 코드가 하나라도 틀리면 무조건
`UPDATE users SET mfa_secret = NULL, mfa_enabled = false WHERE user_id
= $1`을 실행한다. `POST /auth/mfa/verify`(`auth.py:62-72`)는
`get_current_user`(단순 Bearer 토큰)로만 보호되고, `users.py`의 출금
화이트리스트 등록·회원탈퇴처럼 민감한 동작에 이미 쓰이고 있는
`_reauthenticate()`(비밀번호 재확인) 같은 추가 인증이 전혀 없다.

**재현 시나리오**: 이미 MFA를 켜둔 사용자의 Bearer 토큰을 탈취한
공격자(XSS, 로그 유출, 방치된 세션 등 — 비밀번호는 모름)가
`POST /auth/mfa/verify`에 아무 틀린 코드나 한 번만 보내면, 그 즉시
피해자 계정의 MFA가 영구히 꺼진다 — 비밀번호를 몰라도 계정의 보안
수준을 원격으로 영구 하향시킬 수 있고, 이 효과는 탈취한 토큰이
만료된 뒤에도 그대로 남는다.

**권장 수정 방향**: `mfa_enabled`이 이미 `true`인 상태에서의 실패는
그냥 `MfaError`만 던지고 행은 건드리지 않는다 — secret을 폐기하는
현재 동작은 `mfa_enabled=false`(설정 대기 중)일 때만 남긴다. 별도로,
`/mfa/setup`(이미 MFA가 켜진 계정이 이걸 다시 호출하면 기존 secret을
덮어쓸 수 있다는 점도 같은 문제)에 `users.py`의 `_reauthenticate()`
패턴을 적용하는 걸 권장.

---

## 2026-08-29-12 · 로그인 실패 응답이 "동일 메시지"인데도 처리 시간이 경로별로 크게 달라 타이밍 사이드채널로 계정 존재 여부를 알아낼 수 있음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 테스트 10개
통과, ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — 모듈 로드 시 고정 더미 비밀번호로
Argon2 해시(`_DUMMY_PASSWORD_HASH`)를 한 번 계산해두고, 계정
미존재/정지·삭제/잠김 세 빠른 실패 경로 각각에서 실제 사용자 경로와
동일하게 `_hasher.verify()`를 한 번씩 호출(결과는 버림)한 뒤 예외를
던지도록 `_consume_verify_timing()` 헬퍼를 추가했다. Argon2 verify()는
의도적으로 느려서(수십~수백 ms) DB 조회 자체의 변동폭을 압도하므로,
네 경로의 처리시간이 비슷해진다.

**새 테스트**:
`test_nonexistent_account_timing_matches_wrong_password_timing`
(`tests/integration/test_auth_service.py`) — 계정 미존재 경로와
존재하는 계정+틀린 비밀번호 경로의 실제 처리시간을 측정해 비율이
3배를 넘지 않는지 확인(수정 전에는 더미 verify 자체가 없어 이 비율이
훨씬 크게 벌어짐 — Argon2 verify 자체가 유일한 지배적 비용이라
플레이키하지 않음).

**발견**: `src/services/auth_service.py::authenticate()`(134-164행)는
계정 없음/정지·삭제됨/잠김/비밀번호 틀림 네 경우 모두 동일한 일반
메시지를 반환한다고 docstring에 명시돼 있지만, 앞의 세 경우는 즉시
반환하는 반면 마지막(존재하는 계정+틀린 비밀번호)만 느린 Argon2
`_hasher.verify()`를 실제로 호출한다. 이 처리시간 차이가 응답
메시지의 "동일함"이 막으려던 계정 존재 여부 노출을 그대로
가능하게 한다.

**권장 수정 방향**: 계정 미존재/정지/잠김 등 빠른 실패 경로에서도
고정된 더미 해시에 대해 Argon2 `verify()`를 한 번 호출해, 실제 사용자
경로와 처리시간을 비슷하게 맞춘다.

---

## 2026-08-29-13 · TOTP 코드에 재사용(replay) 방지가 없음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 테스트 17개
통과, ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — `users.mfa_last_used_timecode BIGINT`
컬럼을 추가(마이그레이션 `6e70519072a5`)하고, 코드 자체가 유효해도
`pyotp.TOTP.timecode(now)`로 계산한 현재 타임코드가 이미 저장된 값보다
크지 않으면(같은 구간 재사용, 또는 과거 구간) 거부하도록
`_consume_timecode()`를 추가했다 — `WHERE user_id=$1 AND
(mfa_last_used_timecode IS NULL OR mfa_last_used_timecode < $2)`
조건부 UPDATE라 동시에 같은 코드로 두 번 요청해도 하나만 통과한다.
`MfaService.verify()`(최초 설정 검증)와 `verify_totp_for_login()`(로그인
시점) 둘 다 이 경로를 공유한다 — 로그인 콜백 시그니처에 `user_id`가
없어(기존에는 `(secret, code)` 2개 인자) `(user_id, secret, code)` 3개
인자로 확장(`AuthService.VerifyTotpFn` 타입도 함께 갱신).
`MfaService`에 테스트 전용 `now` 콜백 주입 지점도 추가해(watchdog.py의
clock 주입과 동일 원칙) 실제로 30초를 기다리지 않고도 서로 다른
타임코드 구간을 결정적으로 재현할 수 있게 했다.

**새 테스트**: `test_verify_rejects_replaying_the_same_totp_code`
(`tests/integration/test_mfa_service.py`) — 같은 구간, 같은 코드로 두
번째 검증을 시도하면 거부되는지 확인. 기존 테스트 2개(설정 검증 후
로그인, MFA 활성화 후 재검증)도 재사용 방지로 인해 실제 동작이
바뀌어(같은 코드 두 번 통과가 더 이상 허용되지 않음) 주입 clock으로
다음 구간 코드를 만들도록 갱신.

**발견**: `mfa_service.py::_check_code()`가 `pyotp`의 `TOTP.verify()`를
`valid_window` 기본값(0)으로만 호출하고, 성공한 코드를 "이미 썼다"고
기록하는 곳이 어디에도 없다(`users` 테이블 마이그레이션에도 해당
컬럼 없음). 같은 30초 유효구간 안에서는 한 번 유출된 코드를
반복해서 재사용할 수 있다.

**권장 수정 방향**: 사용자별로 마지막으로 성공한 TOTP 타임코드(또는
그 해시)를 저장해두고, 이미 사용한 코드와 같은 타임코드면 유효구간
안이라도 거부.

---

## 2026-08-29-14 · risk_policy.yaml의 수치 필드에 범위 검증이 전혀 없음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 테스트 6개 통과,
ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — 퍼센트류 필드(daily_loss/max_drawdown/
position_concentration/strategy_allocation/var.max_pct/correlation_risk.
aggregate_exposure_max_pct/circuit_breaker 각 단계/watchdog.
loss_threshold_pct/data_distrust 임계치)에 `Field(gt=0, le=100)`,
`var.confidence`에 `Field(gt=0, lt=1)`, `correlation_risk.threshold`(상관계수
크기)에 `Field(gt=0, le=1)`, 배수류(leverage.default_max/coverage_
multiplier/trade_frequency.anomaly_multiplier)에 `Field(gt=0)`, 기간/구간류
(var.horizon_days/watchdog.unresponsive_sec/window_min/data_distrust.
exit_sustain_sec/circuit_breaker의 초 단위 필드/execution_loop.interval_sec)에
`Field(gt=0)`을 추가했다. 실제 `config/risk_policy.yaml` 값은 전부 이
범위 안이라 기존 로딩은 그대로 통과한다.

**새 테스트**: `test_out_of_range_percentage_raises_validation_error`,
`test_position_concentration_over_100_pct_raises_validation_error`,
`test_var_confidence_out_of_unit_interval_raises_validation_error`
(`tests/unit/core/loader/test_risk_policy_loader.py`) — 각각 음수 손실
임계값, 100% 초과 집중도 상한, 0~1 범위를 벗어난 신뢰도를 넣었을 때
`ValidationError`가 발생하는지 확인.

**발견**: `src/core/loader/risk_policy_loader.py`(19-117행)의 모든
`RiskPolicy` 하위 모델이 퍼센트/배수/임계값 필드를 순수 `float`/`int`로만
선언하고 `Field(ge=..., le=...)`나 `@field_validator`가 전혀 없다.
`load_risk_policy()`의 docstring은 "스키마 위반 시 ValidationError로
실패한다 — 조용히 기본값으로 대체하지 않는다"고 명시하지만, 타입만
맞으면 범위를 벗어난 값(음수 손실 임계값, 100% 넘는 집중도 상한, 0으로
비워진 서킷브레이커 임계값 등)도 그대로 통과해 실제 운영 정책이
된다. `config/risk_policy.yaml`은 이 리포에서 FROZEN Zone과 동일하게
취급되는 파일이라, 여기서의 오타 하나가 유일한 방어선이 될 수 있다.

**권장 수정 방향**: 퍼센트류 필드에 `Field(gt=0, le=100)`, 신뢰도류에
`Field(gt=0, lt=1)`, 기간/구간류에 양의 정수 제약 등 필드별 의미에 맞는
범위를 추가.

---

## 2026-08-29-15 · EventBus의 audit_sink 호출 실패가 그 토픽의 워커 태스크를 조용히 죽일 수 있음

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 테스트 9개 통과,
ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — `_handle_safe_error`/
`_handle_critical_error`의 `audit_sink` 호출 각각을 try/except로 감싸
실패해도 로그만 남기고 계속 진행하도록 했다. 심층 방어로
`_worker_loop`의 디스패치 구간(`for handler... await self._dispatch(...)`)
전체도 넓은 try/except로 한 번 더 감쌌다 — `_dispatch`가 이미 handler/
audit_sink 예외를 흡수하지만, 예기치 못한 예외까지 워커 태스크를 죽이는
일은 절대 없어야 한다는 원칙을 코드로 강제한다.

**새 테스트**:
`test_audit_sink_failure_does_not_kill_worker_for_safe_handler`,
`test_audit_sink_failure_does_not_kill_worker_for_critical_handler`
(`tests/integration/test_event_bus.py`) — audit_sink가 예외를 던지도록
구성한 뒤, 같은 토픽에 발행한 두 번째 이벤트가 여전히 정상 처리되는지
확인. 수정 전 코드로 되돌려 SAFE 핸들러 테스트가 실제로 실패(두 번째
이벤트 미수신 + "Task exception was never retrieved" 로그)하는 것까지
직접 확인.

**발견**: `src/core/event_bus/in_process.py`의 `_handle_safe_error`/
`_handle_critical_error`(136-199행)가 핸들러 실패를 감사기록하려고
`await self._audit_sink(...)`를 호출하는데, 이 호출 자체는 try/except로
안 감싸여 있다(반면 몇 줄 아래의 에스컬레이션 `publish()`는 감싸여
있음). `audit_sink`가 예외를 던지면(지금은 로깅 스텁이라 안 던지지만,
FD-7.4 실DB 연동 후에는 커넥션 오류 등으로 던질 수 있음) 그 예외가
`_worker_loop`까지 전파돼 해당 토픽의 워커 태스크 자체가 죽는다 —
아무도 이 태스크를 감시하지 않아서, 이후 그 토픽에 발행되는 이벤트가
(CRITICAL 핸들러 포함) 전부 조용히 처리되지 않는다.

**권장 수정 방향**: `_handle_safe_error`/`_handle_critical_error`의
`audit_sink` 호출도 자체 try/except로 감싸고(로그만 남기고 계속
진행), `_worker_loop`의 디스패치 루프 자체에도 넓은 try/except를
둬서 어떤 예외도 루프를 빠져나가지 못하게 하거나, 워커 태스크에
done-callback을 달아 죽는 즉시 로그+재기동하도록 보강.

---

## 2026-08-29-16 · verification_service.decide()의 REJECT 사유가 DB에도 이벤트에도 저장되지 않음 (보안 아님, 데이터 유실)

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 테스트 7개 통과,
ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: `strategy_listings.rejection_reason TEXT` 컬럼을
추가(마이그레이션 `946d3f25d19a`)하고, REJECT 분기의 UPDATE와
`strategy.verification.completed` 이벤트 페이로드 양쪽에 이 값을
포함하도록 수정했다.

**새 테스트**: `test_reject_reason_is_persisted_and_published`
(`tests/integration/test_verification_service.py`) — 반려 후 DB
컬럼과 발행된 이벤트 페이로드 양쪽에서 사유가 그대로 조회되는지 확인.

**발견**: `verification_service.py::decide()`가 REJECT 시 `rejection_reason`을
필수로 받아 검증까지 하지만(56-58행), 실제 UPDATE(87-91행)도
`strategy.verification.completed` 이벤트 페이로드(103-110행)도 이
값을 포함하지 않는다 — `strategy_listings` 테이블 자체에 해당 컬럼이
없다. 검증담당자가 입력한 반려 사유가 응답이 나간 순간 완전히
사라져서, 판매자는 왜 반려됐는지 다시는 알 수 없다.

**권장 수정 방향**: `strategy_listings`에 `rejection_reason` 컬럼을
추가하고 UPDATE·이벤트 페이로드 양쪽에 포함.

---

## 2026-08-29-17 · strategy_builder_service.transition_lifecycle()도 같은 미조건부 UPDATE 패턴 — 현재는 HTTP 미배선이라 잠재적(dormant)

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — strategy_builder_service
12개 + strategy_builder 라우터 16개 통과, ruff/mypy strict clean). 감사
세션 재검증 대기.

**수정 내용**: 권장 방향 그대로 — UPDATE에 방금 읽은 `current`
(lifecycle_status)를 `AND lifecycle_status = $4` 조건으로 추가하고
`RETURNING`이 빈 행이면 `StrategyLifecycleError`(동시 처리 충돌)를
던진다. 아직 라우터에 배선되지 않아(문서화된 의도적 편차, 여전히
유지) 지금 당장 HTTP로 트리거할 방법은 없지만, 나중에 배선되는
순간 같은 취약점이 재발하지 않도록 지금 하드닝해뒀다. 새 테스트
`test_concurrent_transitions_only_one_succeeds`
(`tests/integration/test_strategy_builder_service.py`)가
`asyncio.gather`로 같은 GENERATED 상태에서 동시에 두 전이를 시도해
정확히 하나만 성공함을 실증(04/05/08/09/16번이 이미 쓴 것과 동일한
회귀 검증 방식).

**발견**: `strategy_builder_service.py::transition_lifecycle()`(143-197행)도
04/05/08번과 같은 패턴(상태를 읽고 검증한 뒤 `WHERE strategy_id=$1 AND
version=$2`만으로 UPDATE, 상태 조건 없음) — 다만 `strategy_builder.py`
라우터에 이 함수가 아직 연결돼 있지 않아(문서화된 의도적 편차) 지금
당장 외부에서 트리거할 방법이 없다. 나중에 자동 백테스트/검증
파이프라인이 이 함수를 호출하도록 배선되는 순간, 예를 들어 관리자의
REJECTED 판정과 자동 파이프라인의 다음 단계 전이가 경합하면 반려된
전략이 조용히 되살아나 거래 가능 상태로 넘어갈 수 있다.

**권장 수정 방향**: 지금 당장 위험하진 않지만, 배선되기 전에
`WHERE strategy_id=$1 AND version=$2 AND lifecycle_status=$3`(방금
읽은 값) 조건을 미리 추가해두는 걸 권장 — 이미 한 번 공들여
하드닝한 파일(`APPROVED` 전이 리스크체크)에 같은 클래스 버그가 또
배선되는 걸 사전에 막는 차원.

---

## 2026-08-29-18 · 거래소 어댑터 사소 항목 2건 (참고용, 우선순위 낮음)

**상태**: 🟢 구현 세션이 수정(이 세션 자체 확인 — 관련 테스트 22개
통과, ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**:
(a) `_BitgetHTTPClient._request()`가 서명용/실제 전송용 쿼리스트링을
`httpx.QueryParams(params)`로 한 번만 인코딩해 양쪽에 동일하게
재사용하도록 변경 — `_client.request(method, request_path, ...)`처럼
이미 쿼리스트링이 붙은 경로를 그대로 넘겨 httpx가 별도로 다시
인코딩하지 않게 했다.
(b) `KISTradingMixin.place_order()`의 `raw["output"]["KRX_FWDG_ORD_ORGNO"]`/
`["ODNO"]` 접근을 `market_data_mixin.py`와 동일한 패턴(try/except
KeyError → `FatalExchangeError`)으로 감쌌다.

**새 테스트**: `test_place_order_missing_expected_field_raises_fatal_exchange_error`
(`tests/integration/test_kis_adapter.py`) — 응답에서 `KRX_FWDG_ORD_ORGNO`
필드를 빼고 호출하면 `FatalExchangeError`가 발생하는지 확인. (a)는
기존 Bitget 어댑터 통합테스트가 그대로 통과하는 것으로 회귀 없음을
확인(전부 영숫자 값이라 인코딩 결과 자체는 이전과 동일 — 이 수정은
향후 퍼센트인코딩이 필요한 값에 대한 예방 조치).

**a) Bitget GET 서명이 수동 쿼리스트링 조합** —
`src/exchanges/bitget/adapter.py`(85-96행)가 서명용 문자열을
`f"{k}={v}"` 수동 join으로 만드는데, 실제 요청은 httpx의 `params=`
인코딩을 따로 탄다. 지금 쓰는 값(symbol/limit/coin 등)은 전부
영숫자라 우연히 일치하지만, 나중에 퍼센트인코딩이 필요한 값이
생기면 서명 문자열과 실제 전송 문자열이 어긋나 서명오류(40012,
`FatalExchangeError`)로 실패한다 — 조용히 잘못되는 게 아니라 크게
실패하는 쪽이라 심각도는 낮음.

**b) KIS trading_mixin의 응답 필드 접근이 다른 곳처럼 안 감싸여 있음** —
`src/exchanges/kis/trading_mixin.py`(62-66행)가
`raw["output"]["KRX_FWDG_ORD_ORGNO"]`류를 다른 메서드들과 달리
`FatalExchangeError`로 감싸지 않고 그대로 접근 — 응답 형식이 바뀌면
설명 없는 `KeyError`가 난다(이것도 조용히 틀린 값이 되는 게 아니라
그냥 실패).

**권장 수정 방향**: (a) 서명용 쿼리스트링과 실제 전송 쿼리스트링을
한 번만 만들어 공유. (b) 다른 메서드처럼 `KeyError`를
`FatalExchangeError`로 감싸 에러 메시지를 통일.

---
