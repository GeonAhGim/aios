# 레드팀 점검 기록

이 문서는 DevEngine 세션(별도 Claude Code 세션, `C:\devengine\mihwa-devengine`
작업 중)이 `C:\aios\mihwa-aios`를 읽기 전용으로 감사하며 찾은 문제를
기록한다 — 원칙적으로 **이 세션은 AIOS 코드를 직접 수정하지 않는다**,
AIOS 구현을 맡은 세션이 이 문서를 보고 판단해서 고치는 용도다.

예외: 2026-09-02, 사용자가 #19~22(HIGH) 4건에 한해 이 세션이 직접
수정하도록 명시적으로 지시했다(다른 세션이 FND-01~09 순서를 따라가느라
기존 코드의 레드팀 항목을 자동으로 챙기지 못하는 공백이 확인된 뒤). 이
4건은 이 세션이 직접 커밋했다 — 아래 각 항목의 FIXED 근거에 표시.
이후로도 기본값은 여전히 "감사만, 수정은 다른 세션"이다.

각 항목은 발견 시점의 실제 코드/테스트 근거를 남기고, 상태(OPEN/FIXED)를
갱신한다. FIXED로 표시된 항목은 감사 세션이 수정 커밋까지 직접 확인한
것이다(자체 보고 아님 — ruff/mypy/pytest 실행 결과로 검증).

---

## 2026-09-02-전수감사 · 설계문서 대 코드 전수 점검 파생 항목 (#35~#40)

`docs/FULL_AUDIT_2026-09-02.md`(PM 세션 agent-platform-12)가 설계문서
00~17·ADR·L3 명세 71~81·표준 103~108 전체를 코드와 대조하며 찾은 항목
중, 이 장부에 번호가 없던 것을 등재한다. 감사 보고서 §2가 원본이며 여기는
장부 정합용 요약이다. #35~#38은 발견 즉시 같은 세션이 수정·검증했고(커밋
`90feab5`), #39는 OPEN, #40은 이미 수정됐으나 장부에 빠져 있던 항목이다.
#25도 같은 세션의 `2e943c9`로 닫혔다(아래 항목 상태 갱신).

---

## 2026-09-02-35 · [marketplace] 음수 가격 리스팅으로 구매자 지갑이 증액됨 — 심각도 높음

**상태**: ✅ FIXED (커밋 `90feab5`, PM 세션 — ruff/mypy/대상 통합테스트 72개 통과)

**발견**: `ListingCreateRequest.price: Decimal | None`에 하한이 없고
`ListingService.create_listing()`도 검사하지 않는다. 구매 시
`wallet_service.debit()`의 `WHERE balance >= $2`는 amount가 음수면 항상
참이라 `balance - (-x)` 로 잔액이 늘어난다. 판매자가 `price=-1000000`으로
등록·검증 통과 후 부계정으로 구매하면 지갑을 원하는 만큼 불릴 수 있었다.

**수정**: 스키마 `Field(ge=0)` + `_validate_price()` + DB
`CHECK (price IS NULL OR price >= 0)`(마이그레이션 `b7e2c4d9f1a6`) 세 겹.
회귀: `test_marketplace_router.py::test_negative_price_listing_is_rejected_at_schema`,
`test_listing_service.py::test_create_listing_rejects_negative_price` 외 1.

---

## 2026-09-02-36 · [marketplace] 구매 Idempotency-Key가 사용자 스코프 없이 전역 + 실패 응답까지 캐시 — 심각도 높음

**상태**: ✅ FIXED (커밋 `90feab5`)

**발견**: 라우터가 `purchase:{header}`만으로 키를 만들어(`marketplace.py:155-156`)
사용자 B가 A와 같은 헤더값을 보내면 A의 캐시된 `PurchaseResponse`
(purchase_id·정산액)를 받고 B의 구매는 일어나지 않았다. 또 402/400도
캐시돼(`core/idempotency.py:37-43`) 잔액 부족 후 충전해도 같은 키로는
영원히 402였다. 캐시 조회→compute→INSERT가 비원자적이었고, 커넥션을 쥔 채
compute() 안에서 풀을 다시 잡아 풀 크기 10에서 교착 가능했다.

**수정**: 키를 `purchase:{user_id}:{header}`로. `with_idempotency`를
claim-first로 재작성 — 자리표시자(status 0) 선점 → 같은 키 동시 요청은
409 → 2xx만 저장, 4xx/5xx·예외는 선점 해제 → compute() 전에 커넥션 반납.
회귀: `test_idempotency_key_is_scoped_per_user`,
`test_failed_purchase_is_not_cached_under_idempotency_key`.

---

## 2026-09-02-37 · [marketplace] 동일 리스팅 중복 구매·환불 이중 적립 — 심각도 높음

**상태**: ✅ FIXED (커밋 `90feab5`)

**발견**: `strategy_purchases`에 UNIQUE(listing_id, buyer_user_id)가 없고
`PurchaseService.purchase()`도 기존 구매를 확인하지 않아 다른 키로
재요청하면 두 번 차감·정산됐다. `DisputeResolutionService.resolve()`는
RESOLVED 뒤 같은 구매에 새 분쟁을 열고 다시 `DELISTED_AND_REFUND`하면
`credit(REFUND)`을 재적립했다(구매행에 환불 상태 없음).

**수정**: `purchase()`가 리스팅 `FOR UPDATE` 잠금 안에서 기존 구매를 조회해
거부(경합 안전) + UNIQUE 인덱스 `uq_strategy_purchases_listing_buyer`;
`strategy_purchases.refunded_at` 조건부 UPDATE로 환불 1회 보장(이미 환불이면
분쟁 RESOLVED 전이까지 롤백). 회귀:
`test_same_buyer_cannot_purchase_same_listing_twice`,
`test_refund_is_credited_only_once_across_disputes`.

---

## 2026-09-02-38 · [marketplace] 검증담당자가 자기 리스팅을 승인할 수 있음 — 15번 §15.6 "API 레벨 강제" 규칙 미구현 — 심각도 높음

**상태**: ✅ FIXED (커밋 `90feab5`)

**발견**: `VerificationService.decide()`가 `seller_user_id`를 읽고도
`verifier_id`와 비교하지 않았다(`verification_service.py:71-80`).
대기열 필터(`verification_queue_service.py:37-42`)는 본인 리스팅을
숨기지만 listing_id를 직접 지정한 호출은 막지 못한다. 테스트도 없었다.

**수정**: `pre_check["seller_user_id"] == verifier_id`이면
`VerificationError("이해상충")`. 회귀:
`test_verification_service.py::test_verifier_cannot_decide_own_listing`.

---

## 2026-09-02-39 · [execution_loop] 취소·거부·만료로 끝난 주문 뒤 FSM이 BUY/SELL_ORDER_PENDING에 영구 고착 — 심각도 높음

**상태**: 🔴 OPEN (agent-platform-9f 배정 — 감사 보고서 §2-A)

**발견**: 실행 루프를 운영 앱에 배선(`2e943c9`)하면서 확인. tick의
`_handle_pending_fill_check`는 최신 주문이 최종 상태면 즉시 return하고
FILLED일 때만 `apply_fill` + FSM 전이를 한다(`tick.py:100-140`).
`cancel.py`는 orders.status만 CANCELLED로 바꾸고 FSM은 건드리지 않는다.
즉 주문이 CANCELLED/REJECTED/EXPIRED/FAILED로 끝나면 fsm_state가
`*_ORDER_PENDING`에 남아 그 실행은 영원히 새 신호를 평가하지 않는다.
재시작 복구(`recovery_wiring.py`)는 이 이유로 FILLED를 쓰지 않고 tick에
위임하며, 취소·거부만 영속화한다 — 그 뒤의 FSM 복귀는 이 항목의 몫이다.

**권장 수정 방향**: `_handle_pending_fill_check`에서 최종 상태가 FILLED가
아니면 FSM을 PENDING 진입 전 상태(IDLE 또는 HOLDING — FSM 정의의
역전이로 결정)로 조건부 갱신하고 `order.status.changed`를 발행. cancel.py는
그대로 두고 tick 한 곳에서 처리하면 복구·취소·거부 세 경로가 모두 해결된다.

---

## 2026-09-02-40 · [connections] CON-004 revoke/sync TOCTOU — 수정됐으나 장부 누락 (정합 보정)

**상태**: ✅ FIXED (커밋 `bbec6c6`, 재확인+저장을 `FOR UPDATE` 트랜잭션으로
원자화 — `test_real_concurrent_revoke_and_sync_never_leaves_post_revocation_snapshot`)

**메모**: 수정 커밋 메시지와 코드 주석에만 존재하고 이 장부에 항목이
없었다(감사 보고서 §9). 추적성을 위해 등재만 한다.

---

## 2026-09-02-재감사 · FND-04~09 + Bitget 신규 확장 API 3개 영역 병렬 감사 완료

91eee31(제 백테스트 커밋) 이후 origin/main에 새로 쌓인 커밋 전부를
스코프로 3개 에이전트에게 병렬 감사를 맡겼다 — (1) FND-06 Risk &
Safety Gate(킬스위치), (2) Bitget Subaccount/Loan/Margin/Futures P1,
(3) Bitget WebSocket 프라이빗 채널 + Convert/Grid/Strategy. 가장 심각한
#26/#27(킬스위치 캐시 레이스, PROVIDER 범위 미조회)은 이 세션이 직접
`evaluate_risk_gate.py`/`postgres_repository.py`를 읽어 재확인했다.
나머지는 에이전트 보고를 그대로 옮기되, 코드 인용이 구체적이라 신뢰도가
높다고 판단해 별도 재검증 없이 기록한다(후속 세션이 필요시 재확인).

---

## 2026-09-02-26 · [FND-06] 킬스위치 평가가 캐시를 먼저 확인해, 활성화 직후 최대 10초간 이미 발동된 킬스위치를 우회할 수 있음 — 심각도 높음

**상태**: ✅ FIXED — `RiskGateRepository.invalidate_evaluations(tenant_id)`
신설, `activate_safety_control()`/`deactivate_safety_control()`이
통제 변경 직후 호출(GLOBAL/PROVIDER는 전체 tenant 무효화, TENANT/
ACCOUNT는 해당 tenant만). 회귀 테스트
`test_kill_switch_after_cached_allow_takes_effect_immediately`,
`test_global_kill_switch_invalidates_cached_allow_for_every_tenant`로
확인(먼저 ALLOW를 캐시시킨 뒤 킬스위치를 걸고, 같은 fingerprint로
즉시 재평가해 DENY 확인). fence_token 실제 검증(근본 해법)은 이번
스콥에 포함하지 않음 — 캐시 무효화만으로 이 재현 시나리오는 닫힘.

**발견**: `src/foundation/risk_gate/application/evaluate_risk_gate.py:73-75` —
`evaluate_risk_gate()`가 `repo.get_cached_evaluation(tenant_id,
fingerprint)`를 **가장 먼저** 확인하고, 캐시가 있으면(`EVALUATION_CACHE_
TTL_SECONDS = 10`, 33행) `repo.list_active_controls()`(97행, 실제
킬스위치 조회)를 **호출조차 하지 않고** 캐시된 과거 판정을 그대로
반환한다. `activate_safety_control()`/`insert_safety_control()`
(postgres adapter) 어디에도 이 tenant의 기존 `risk_evaluation` 캐시
행을 무효화하는 코드가 없다. fingerprint는 `tenant_id | gate_kind |
connection_id | plan`로 결정되는데, `paper_control/start_deployment.py`는
`plan`을 넘기지 않아 같은 배포/커넥션에 대한 반복 호출은 같은
fingerprint로 수렴한다 — 이 세션이 직접 코드를 읽어 재확인함(자체
보고 아님).

**재현 시나리오**: T0에 트레이더가 배포 D를 `:start` — 킬스위치 없음
→ ALLOW가 캐시에 10초간 저장됨. T0+2초에 운영자가 GLOBAL 킬스위치를
`POST /admin/safety-controls`로 발동(응답 201, `GET /safety-controls`에도
정상 표시됨). T0+2~10초 사이에 같은 배포/커넥션으로 `:start`나
`:resume`을 다시 호출하면(같은 fingerprint) 캐시가 살아있어 방금
발동한 GLOBAL 킬스위치를 전혀 거치지 않고 stale ALLOW를 그대로
반환 — 배포가 RUNNING으로 전이된다. `SafetyControl.fence_token`이
존재하고 원자적으로 증가는 하지만(postgres_repository.py 91-120행),
`evaluate_risk_gate`도 `start_deployment.py`도 이 토큰을 한 번도
읽거나 비교하지 않아 78번 스펙 RSK-005(활성화-제출 레이스를 fence로
방지)가 실제로는 구현돼 있지 않다.

**영향**: "최종 거부권(veto)"이라는 이 계층의 존재 이유 자체가 무너진다
— 운영자가 명시적으로 긴급정지를 걸었는데도 최대 10초는 여전히 새
배포가 뚫고 나갈 수 있다. 성능 트레이드오프가 아니라 안전 결함으로
봐야 한다(78번 §2 "새 제한 규칙은 즉시 적용" 요구사항 위반).

**권장 수정 방향**: 캐시 적중 여부와 무관하게 `list_active_controls()`는
매번 새로 조회(그 자체는 저렴한 조회)하거나, `insert_safety_control()`
실행 시 해당 tenant의 `risk_evaluation` 캐시 행을 무효화/삭제. 근본적으로는
fence_token을 실제로 검증하는 경로(평가 시점 토큰을 기록해두고 상태
전이 직전 토큰이 그대로인지 재확인) 구현을 권장.

---

## 2026-09-02-27 · [FND-06] PROVIDER 범위 킬스위치가 실제 평가 경로에서 단 한 번도 조회되지 않음 — 심각도 높음

**상태**: ✅ FIXED — `evaluate_risk_gate()`가 connection의
`provider_code`를 `list_active_controls(provider_code=...)`로 전달.
Control Center 목록(`projections.py`)은 `include_all_providers=True`로
특정 provider에 안 좁히고 PROVIDER 범위 전체를 노출하도록 별도 반영.
회귀 테스트 `test_provider_scope_kill_switch_denies_connection_on_
that_provider`(fake connection repo로 provider_code="binance" 고정,
PROVIDER 킬스위치 발동 후 DENY+RISK_KILL_SWITCH_ACTIVE_PROVIDER 확인)로
검증.

**발견**: `src/foundation/risk_gate/adapters/postgres_repository.py::
list_active_controls(*, tenant_id, provider_code=None)` — `provider_code`가
주어질 때만 PROVIDER 범위를 쿼리 조건에 추가한다(직접 코드 확인).
그런데 `evaluate_risk_gate.py:97`의 유일한 호출부는
`repo.list_active_controls(tenant_id=tenant_id)`로 `provider_code`를
**절대 넘기지 않는다** — `connection_repo.get_connection()`으로 이미
connection을 조회해(91행) `.provider_code` 필드까지 갖고 있으면서도
전달하지 않는다. 저장소 전체에서 `list_active_controls`를 호출하는
곳은 이 함수와 `projections.py` 단 두 곳뿐이며 둘 다 동일하게
`provider_code`를 넘기지 않는다.

**재현 시나리오**: 운영자가 `scope=PROVIDER, scope_ref="BINANCE"`로
킬스위치를 발동(성공, DB에 정상 저장) → 이후 Binance 커넥션에 대한
모든 `evaluate_risk_gate()` 호출은 이 행을 영원히 못 보고 계속
ALLOW를 낼 수 있다. `GET /safety-controls` Control Center 목록에도
누락돼(같은 근본 원인) 운영자가 "왜 안 먹히지"조차 알아챌 방법이 없다.

**영향**: "이 거래소만 멈춰라"라는, 실무에서 가장 자주 쓰일 법한
긴급조치 하나가 통째로 무동작이다. #26과 결합하면 더 심각 — 캐시
문제를 고쳐도 PROVIDER 범위 자체는 여전히 죽어있다.

**권장 수정 방향**: `evaluate_risk_gate()`에서 이미 조회한
`connection.provider_code`를 `list_active_controls(tenant_id=...,
provider_code=connection.provider_code)`로 그대로 전달. Control
Center 목록(`build_safety_control_list_view`)에도 PROVIDER 범위가
노출되도록 반영. "GLOBAL/PROVIDER 킬스위치가 실제로 ALLOW를
막는다"는 회귀 테스트 추가 권장.

---

## 2026-09-02-28 · [FND-06] STRATEGY_DEPLOYMENT 범위는 API로 생성 가능하지만 이를 조회하는 코드가 아예 없음 — 의도된 제약이나 API가 이를 알리지 않음 — 심각도 중간

**상태**: 🟢 문서로 명확화(기능 변경 없음) — `activate_safety_control.py`
모듈 docstring에 "지금 생성해도 아직 어떤 평가에도 영향을 주지
못한다"는 경고를 명시적으로 추가. 원 작성자의 의도(FND-07 이전
선반영, 운영자 전용)를 존중해 요청 자체를 거부하지는 않기로 판단 —
API 응답 필드에 경고를 싣는 계약 변경까지는 이번 스콥에서 하지
않음(v1 계약 안정성 우선).

**발견**: `list_active_controls`가 조회하는 범위는 `{GLOBAL, TENANT,
ACCOUNT}` (+옵션 PROVIDER)뿐 — `STRATEGY_DEPLOYMENT`를 조회하는
코드 경로는 저장소 어디에도 없다(`grep risk_gate src/services/`도
0건 — 주문 실행 경로 자체가 이 모듈과 아직 연결 안 됨, 마이그레이션
`c7d4e1a9f052...py` 주석이 이를 "아직 배선 안 함"으로 명시). 그런데
`ActivateSafetyControlRequest`/`SafetyScope`(contracts/v1.py)는
`STRATEGY_DEPLOYMENT`를 정상 값으로 받아들이고 `POST
/admin/safety-controls`가 201로 성공 응답한다.

**재현 시나리오**: `scope=STRATEGY_DEPLOYMENT, scope_ref=<배포ID>`로
킬스위치 생성 → 성공 응답, DB에 저장, fence_token 증가까지 전부
정상처럼 보이지만 이 값을 소비하는 코드가 전무해 어떤 평가에도
영향을 못 준다.

**영향**: #27과 같은 "성공 응답인데 사실 무동작" 패턴. 다만 이건
FND-07(주문 어댑터) 부재라는 명시적으로 인지된 스코프 제약이라
버그라기보다 API가 그 한계를 호출자에게 알리지 않는 게 문제.

**권장 수정 방향**: FND-07 전까지는 `STRATEGY_DEPLOYMENT` 요청을
400으로 거부하거나, 응답에 "아직 집행되지 않음" 경고 필드를 포함.

---

## 2026-09-02-29 · [FND-06] scope_ref 누락 시 검증 없이 조용히 무효한 TENANT/PROVIDER 킬스위치가 생성됨 — 심각도 중간

**상태**: ✅ FIXED — `activate_safety_control()`에 `MissingScopeRefError`
추가, TENANT/PROVIDER/STRATEGY_DEPLOYMENT 범위에서 `scope_ref`가
비어있으면 즉시 거부. 라우터(`/safety-controls`, `/admin/safety-controls`
둘 다)에서 400으로 변환. 회귀 테스트
`test_activate_missing_scope_ref_for_tenant_scope_is_rejected`로 확인.

**발견**: `application/activate_safety_control.py:56-59` —
`GLOBAL`만 `scope_ref`를 강제하고, 나머지 범위는 `resolved_ref =
scope_ref or ""`로 누락 시 빈 문자열로 조용히 대체한다.
`ActivateSafetyControlRequest.scope_ref`도 `str | None = None`이라
스키마 레벨 검증이 없다. `list_active_controls`는 TENANT 행을 항상
실제 tenant UUID 문자열로 조회하므로, `scope_ref=""`로 저장된 행은
영원히 매치될 수 없는 고아 행이 된다.

**재현 시나리오**: `POST /admin/safety-controls {"scope":"TENANT",
"reason":"..."}`(scope_ref 누락) → 201 성공, `scope_ref=''`로 저장 →
어떤 평가도 이 행을 못 찾음.

**권장 수정 방향**: `scope in {TENANT, PROVIDER, STRATEGY_DEPLOYMENT}`인데
`scope_ref`가 비어있으면 Pydantic validator + 애플리케이션 계층
양쪽에서 400 거부.

---

## 2026-09-02-30 · [FND-06] 킬스위치가 아직 실제 주문 제출/실행 경로에 배선돼 있지 않음 — 이미 인지된 스코프 제약, 버그 아님(참고용)

**상태**: 🟢 문서로 명확화(기능 변경 없음) — `activate_safety_control.py`
모듈 docstring에 "신규 배포 시작만 차단, 이미 RUNNING인 배포는 영향
없음, 기존 execution_loop 킬스위치와는 별개 시스템" 경고를 명시적으로
추가. 코드 수정 대상이 아니라는 원래 판단 유지.

**발견**: `grep risk_gate src/services/`는 0건. 현재 유일한 실제
연동 지점은 `paper_control.start_deployment`/`resume_deployment`(배포가
RUNNING으로 "전이"할 때만 게이트) — 이미 RUNNING인 배포가 계속
주문을 내는 것은 이 게이트로 전혀 막지 못한다. 마이그레이션 주석이
이를 명시적으로 인지하고 있다("71번 §1 FROZEN 영역 미변경").

**영향**: "GLOBAL 킬스위치를 걸면 전체 거래가 멈춘다"고 가정하면
틀렸다 — 지금은 "새 배포 시작만 막는다". 기존 킬스위치
메커니즘(레드팀 #08 대상, execution_loop 쪽)과는 별개 시스템이다.

**권장 수정 방향**: 코드 수정보다 운영 커뮤니케이션 — 킬스위치
활성화 응답에 "신규 배포 시작만 차단, 이미 RUNNING인 배포는 FND-07
전까지 영향 없음" 같은 경고를 포함해 운영자가 과신하지 않도록.

---

## 2026-09-02-31 · [Bitget WS] 프라이빗 채널 재연결 시 로그인 서명을 재사용해 재연결 이후 인증이 조용히 영구 실패할 수 있음 — 심각도 높음(현재 호출부 없어 도달 불가)

**상태**: ✅ FIXED — `_run_ws_subscription()`의 `pre_messages`(고정
list)를 `pre_messages_factory`(매 연결 시도마다 새로 호출되는
callable)로 전환, `subscribe_order_stream`/`subscribe_account_stream`/
`subscribe_positions_stream` 3곳 모두 갱신. login 성공/실패도
`event=="login"` 분기에서 명시적으로 `logger.info`/`logger.warning`으로
표면화(이전엔 다른 control 메시지처럼 조용히 버려짐). 회귀 테스트
`test_reconnect_resends_login_with_fresh_timestamp_not_stale_one`(`time.time()`을
결정적으로 증가시켜, 재연결 시 timestamp/sign이 실제로 달라지는지
확인)로 검증.

**발견**: `src/exchanges/bitget/market_data_mixin.py::_build_login_message()`가
`subscribe_order_stream()` 등 호출 시 **한 번만** 실행돼 그 시점
타임스탬프로 서명한 `login_msg`를 만들고, 이 고정 dict가
`_run_ws_subscription()`의 `pre_messages`로 전달돼 최초 연결이든
재연결이든 **매번 같은 stale 서명을 재전송**한다(221-266행,
441-553행). `_is_control_message()`가 login/error 이벤트를 그냥
버려(111-112, 143-144행) 로그인 실패가 예외로도 로그로도 드러나지
않는다.

**재현 시나리오**: 연결 → 정상 로그인 → 몇 분 뒤 네트워크 단절로
재연결(백오프 1s→...→30s) → 매 재연결마다 오래된 타임스탬프로 서명된
동일 메시지 재전송 → Bitget이 타임스탬프 범위 초과로 거부(추정,
관례상 초 단위 허용 범위) → orders/account/positions 채널이
겉으로는 "연결됨"인데 실제로는 데이터를 영원히 못 받음.

**영향**: 아직 이 스트림을 실제로 소비하는 코드가 없어(전체 검색
결과 호출부는 mixin 자신과 테스트뿐) 지금 당장은 도달 불가능한
잠재 결함. 다음 세션이 FD-4.5/FD-16.4용으로 이 콜백을 배선하면
"3회 폴링 폴백"만 믿고 실시간 스트림 신뢰도를 과대평가하게 된다.

**권장 수정 방향**: `pre_messages`를 고정 리스트가 아니라 매 연결
시도마다 재평가되는 팩토리로 바꿔 재연결마다 새 타임스탬프로
재서명. login 실패 이벤트(`event=="login" and code != "0"`)를
구분해 최소 `logger.warning`으로 표면화.

---

## 2026-09-02-32 · [Bitget] Convert/Grid/Strategy 및 Subaccount/Loan/Margin/Futures 확장 메서드가 Executor의 LIVE 하드가드·멱등성을 전혀 거치지 않음 — 심각도 높음(구조적, 현재 호출부 없어 도달 불가)

**상태**: ✅ FIXED(부분) — `src/exchanges/common/live_guard.py`에
`@require_paper_sandbox` 공용 데코레이터 신설(`self.is_paper_trading
and self.is_sandboxed`가 아니면 `FrozenZonePaperAdapterBlockedError`).
주문/자금 이동성 메서드 전부에 적용: `execute_convert`,
`place_spot_grid`/`place_futures_grid`, `place_strategy_order`,
`place_margin_order`/`borrow_margin`/`repay_margin`,
`place_futures_order`/`place_futures_tpsl_order`/
`place_futures_position_tpsl`/`place_futures_plan_order`,
`borrow_loan`/`repay_loan`/`revise_loan_pledge`,
`create_subaccount_apikey`/`transfer_to_subaccount`(15개 메서드).
**"부분"인 이유**: Executor의 두 가드 중 `is_sandboxed`만 재현
가능하다 — `mode != "PAPER"` 검사는 실행 레코드라는 adapter가 모르는
호출자 컨텍스트라 데코레이터로 못 만든다. cancel/modify/close류
(risk-reducing)는 의도적으로 가드하지 않음. 각 파일에 회귀
테스트(`test_*_blocked_on_live_configured_adapter`) 추가, 총 82→94개
Bitget 확장 테스트 통과.

**발견**: `Executor.execute()`(`src/core/executor/executor.py:71-85`)만
`mode != "PAPER"` 하드차단 + `is_paper_trading`/`is_sandboxed` 이중
확인 + `submit_order()`의 claim-first 멱등성(#19에서 고친 바로 그
경로)을 거친다. 반면 이번에 새로 추가된
`convert_mixin.py::execute_convert`, `grid_mixin.py::place_spot_grid/
place_futures_grid`, `strategy_mixin.py::place_strategy_order`,
`margin_mixin.py::place_margin_order/borrow_margin/repay_margin`,
`futures_trading_mixin.py::place_futures_order/place_futures_tpsl_order/
place_futures_plan_order`, `loan_mixin.py::borrow_loan/repay_loan/
revise_loan_pledge`, `subaccount_mixin.py::create_subaccount_apikey/
transfer_to_subaccount`는 전부 `self._request()`로 거래소에 직접
도달하며 이 네 가드(mode 체크, sandbox 체크, 검증, idempotency) 중
무엇도 거치지 않는다. `margin_mixin.py::borrow_margin`만 "이 게이트를
강제하지 않는다"는 경고 docstring이 있고, 나머지는 그런 경고조차
없어 문서화 수준이 불균일하다.

**재현 시나리오**: `build_adapter("bitget", key, secret, extra,
demo_mode=False)`로 만든 LIVE adapter에 대해 이 메서드들 중 아무거나
직접 호출하면 FrozenZone 어떤 검사도 거치지 않고 즉시 거래소에
요청이 나간다. 저장소 전체 검색 결과 이 메서드들의 호출부는 각자의
mixin·테스트뿐이라 **지금은 도달 불가능**.

**영향**: 다음 세션이 이 메서드들을 라우터/전략 로직에 배선할 때
Executor와 동등한 가드를 다시 만들어 넣는 걸 잊으면, 그리드봇/TWAP/
환전/대출/서브계정 이체가 승인 절차 없이 LIVE로 나갈 수 있다.

**권장 수정 방향**: Executor가 쓰는 이중 가드(mode + is_sandboxed)를
재사용 가능한 공용 데코레이터/래퍼로 뽑아, 이 신규 메서드 계열이
배선될 때 강제로 걸리도록 인터페이스 수준에서 표준화 권장. 최소한
`borrow_loan`/`create_subaccount_apikey`/`transfer_to_subaccount`에도
`borrow_margin`과 동일한 "게이트 미강제" 경고 docstring부터 추가.

---

## 2026-09-02-33 · [Bitget] Convert/Grid/Strategy/Margin/Loan 신규 메서드에 금액·가격 로컬 검증이 전무 — 심각도 낮음~중간(기존 관례와 대체로 일관, #32와 결합 시 위험 증폭)

**상태**: ✅ FIXED — #32와 같은 커밋으로 처리. 양수 금액/가격, grid의
`lower_price < upper_price`, `grid_count > 0` 등 최소 sanity check를
각 메서드 진입부에 추가(0/음수/역전된 범위는 `ValueError`). 회귀
테스트로 대표 케이스 확인(`test_execute_convert_rejects_non_positive_
amount`, `test_place_spot_grid_rejects_inverted_price_range`,
`test_borrow_margin_rejects_non_positive_amount`,
`test_borrow_loan_rejects_non_positive_pledge_amount`).

**발견**: `from_amount`(convert), `lower_price`/`upper_price`/
`grid_count`/`investment`(grid), `total_amount`/`price`/
`duration_seconds`(strategy), 대출/마진 금액 전부 0/음수/역전된
범위(`lower_price >= upper_price`) 등 어떤 사전 검증도 없이 그대로
직렬화돼 나간다. 기존 `trading_mixin.place_order()`도 자체 검증은
없지만 그건 상위 `Executor.execute() → validate_order_params()`가
걸러준다는 전제(§8.3 원칙)인데, 이 신규 메서드들은 그 상위 계층
자체가 없다(#32와 동일 근본원인).

**권장 수정 방향**: 이 메서드들을 실제로 소비하는 호출부를 만들
때 `validate_order_params()`에 준하는 최소 검증(양수 금액,
`lower_price < upper_price`, 유효한 symbol 형식 등)을 반드시 포함.

---

## 2026-09-02-34 · [Bitget] 서브계정 API 키 생성 시 permissions 생략 가능 — 실제 기본 권한이 read/trade/withdraw 중 무엇인지 코드로 확인 불가 — 심각도 중간

**상태**: ✅ FIXED — `permissions=None`이면 거래소의 미지정 기본값에
맡기지 않고 명시적으로 `["read"]`를 `permType`에 보내도록 수정.
회귀 테스트 `test_create_subaccount_apikey_defaults_to_read_only_
permission`로 확인. Demo API 키로 거래소 실제 기본 동작을 라이브
검증하는 건 이 세션에서 못 함(Demo 키 미보유, 모듈 자신이 이미
인정하는 한계) — 후속 세션 과제로 남음.

**발견**: `subaccount_mixin.py::create_subaccount_apikey`에서
`permissions: list[str] | None = None`이고, `None`이면 `permType`
필드 자체를 요청 바디에서 생략한다. Bitget이 `permType` 미지정 시
어떤 기본 권한을 부여하는지 코드/주석 어디에도 명시돼 있지 않고
(모듈 docstring 자체가 "라이브 검증 필요"라고 인정), 클라이언트
측에서 명시적 최소권한(read-only)을 강제하지 않는다. 이 프로젝트의
"거래 권한 ≠ 출금 권한" 원칙(7.9)과 직결 — 서브계정 키에 출금
권한까지 부여 가능한지도 미확인.

**권장 수정 방향**: `permissions`를 필수 인자로 바꾸거나 `None`일
때 `["read"]`를 명시적으로 채워 보내도록 수정. Demo API 키로 실제
기본 동작 라이브 검증 필요(모듈 자신이 이미 요구하는 작업).

---

## 2026-09-02-25 · risk_guard_loop도 alert_evaluation_loop과 같은 클래스 — 예외 시 손실한도 자동정지 루프가 영구 정지 가능 (참고용, #21 수정 중 발견)

**상태**: ✅ FIXED (커밋 `2e943c9`, PM 세션 — `main.py::_risk_guard_loop()`에
alert 루프와 동일한 try/except 추가. 같은 커밋이 실행 루프 스케줄러·재시작
복구·Circuit Breaker check_reactivation 루프도 배선했으므로, 이 루프가
감시하는 `positions` PnL이 실제로 채워지는 것은 #39 및 감사 §2-A "positions
미기록" 항목(9f)에 달려 있다)

**발견**: #21을 고치며 `src/main.py`를 보다가 발견 — `_risk_guard_loop()`
(`main.py`)도 `_alert_evaluation_loop()`와 정확히 같은 구조다:
```python
async def _risk_guard_loop() -> None:
    while True:
        await asyncio.sleep(RISK_GUARD_INTERVAL_SECONDS)
        await risk_guard_service.evaluate_all_running()
```
`evaluate_all_running()` 내부에서 실행 하나 평가 중 예상 못한 예외가
나면(#21과 동일한 종류의 미검증 입력/일시적 조회 실패 등) 이 루프
자체가 죽어 재시작 전까지 **모든 실행의 손실한도 자동정지 감시가
멈춘다** — 알림 기능보다 안전 크리티컬도가 높은 루프라 알림(#21)보다
잠재 영향이 크다고 판단해 별도 항목으로 남긴다. `evaluate_all_running()`
내부에 이미 개별 실행 단위 try/except가 있는지는 확인하지 않았다(이번
세션은 #19~22로 스콥이 한정돼 이 함수 본문은 읽지 않음).

**권장 수정 방향**: #21과 동일한 2단 방어 — (1)
`RiskGuardService.evaluate_all_running()` 내부에서 실행 하나 평가
실패가 다른 실행 평가를 막지 않는지 확인/보강, (2) `main.py`의
`_risk_guard_loop()`도 `await risk_guard_service.evaluate_all_running()`
호출을 try/except로 감싸 다음 주기 재시도가 되도록.

## 2026-09-02-재검증 · 01~18번 전수 재검증 완료

이전 재검증 대기(🟢) 항목 전체를 이 세션이 직접 실행해 확인했다 —
`TEST_DATABASE_URL`을 `aios_test`(격리된 DB, `docker-compose.dev.yml`의
postgres 컨테이너)로 설정한 뒤 `pytest`(709 passed, 0 failed),
`ruff check .`(all checks passed), `mypy src`(222 files, no issues)를
전부 직접 실행. 01~18번 전 항목이 자체 보고가 아니라 실제 실행 결과로
검증됨. 03번(버그 아님)·18번(참고용) 제외 나머지는 이제 ✅ FIXED로
간주해도 된다 — 다만 상태 라벨 자체는 각 항목 원문 그대로 두고 여기
한 곳에 재검증 결과만 기록한다(각 항목을 개별로 다시 쓰지 않기 위함).

이어서, 아직 아무도 감사하지 않은 최근 커밋(`f74204a` FD-4/FD-8 주문
전송+판단 계층, `826f7fa` 가격/지표 알림, `0a19589`/`0eb5c68`/`d9cb7c5`/
`cf502cd` 신규 엔드포인트들)을 새로 감사해 아래 19~24번을 추가한다.
19~22번은 이 세션이 실제 코드를 직접 읽어 재확인했다(19/20/21번은
해당 파일 원문을 직접 Read로 대조).

---

## 2026-09-02-19 · order_service.submit_order()의 멱등성 체크가 TOCTOU — 거래소엔 나갔지만 DB엔 없는 고아 주문이 발생할 수 있음 — 심각도 높음

**상태**: ✅ FIXED — `submit.py::submit_order()`를 "거래소 호출 전에
INSERT로 client_order_id를 원자적으로 선점 → 거래소 호출 → 실패 시
claim 행 삭제 → 성공 시 conditional_update로 갱신" 순서로 재구성.
회귀 테스트 `test_submit_order_concurrent_calls_only_send_to_exchange_once`
(동시 호출 시 `place_order_call_count == 1` 검증, `asyncio.gather` +
인위적 지연으로 경합 창을 넓힘)로 확인. 기존
`test_submit_order_network_error_propagates`("전송 실패 시 DB에 흔적
안 남음")도 그대로 통과 — claim 삭제로 그 불변조건 유지. 전체
스위트 1003 passed로 직접 실행 검증(자체 보고 아님).

**발견**: `src/services/order_service/submit.py::submit_order()`가 (1)
`repository.get_by_client_order_id`로 기존 주문 존재 여부를 확인 →
(2) 없으면 `adapter.place_order(order)`로 실제 거래소에 주문 전송 →
(3) `repository.insert`로 DB에 영속화, 순서로 진행한다. 이 세 단계
사이에 잠금이나 단일 트랜잭션이 없다. `orders.client_order_id`는 DB
UNIQUE 제약이 걸려 있지만, 그 제약 위반(`asyncpg.UniqueViolationError`)을
`submit_order()` 어디서도 잡지 않는다.

**재현 시나리오**: 같은 `client_order_id`로 `submit_order()`가 거의
동시에 두 번 호출되면(중복 디스패치, 클라이언트 재시도 등) 둘 다 (1)
단계에서 "기존 주문 없음"을 확인하고 통과, 둘 다 (2)단계에서 실제
거래소에 주문을 전송할 수 있다(거래소가 `client_order_id`를 서버측에서
멱등키로 인식해 중복을 걸러줄 수도 있으나, `ExchangeAdapter` 인터페이스
계약에는 그 보장이 없고 KIS 어댑터의 실제 동작은 확인되지 않았다).
(3)단계에서 하나는 정상 insert, 다른 하나는 UNIQUE 위반으로 예외가
발생하는데 — **이 호출자 입장에서는 실제로 거래소에 주문을 넣었는데도
예외를 받는다.** 그 주문은 DB에 없고, `order_id`도 없고, FD-4.5
UNKNOWN 재조회 로직도 이 주문의 존재 자체를 모른다 — 완전히 고아
상태로 거래소에만 살아있는 실제 주문이 된다. 동시성 없이도, "거래소
전송 성공 → DB insert 시점에 커넥션 끊김" 같은 일반적인 일시 장애
만으로도 동일한 고아 주문이 생길 수 있다.

**영향**: PAPER 모드에서는 실제 자금 위험은 없지만, FakeExchangeAdapter가
아닌 실제 거래소 sandbox와 연결되는 순간부터는 실제(모의)계좌에 추적
불가능한 포지션이 남는다 — 이 시스템이 요구하는 "모든 주문은 DB에
영속화되고 추적 가능해야 한다"는 전제 자체가 깨진다.

**권장 수정 방향**: (2)/(3) 사이 순서를 뒤집기는 어렵지만(거래소가
먼저 진실의 원천), 최소한 (3)의 `UniqueViolationError`를 명시적으로
잡아 "이미 처리된 client_order_id — 거래소엔 전송됐을 수 있으니
재조회로 확인" 흐름으로 연결해야 한다. 근본적으로는 (1)+(2)+(3) 전체를
`client_order_id` 기준 advisory lock 또는 DB 레벨 `INSERT ... ON
CONFLICT DO NOTHING`을 먼저 걸어 "이 주문을 시도할 권리"를 원자적으로
선점한 뒤에만 거래소를 호출하는 순서로 재구성하는 걸 권장.

---

## 2026-09-02-20 · order_service.repository의 update_from_exchange/update_after_modify가 이미 고친 줄 알았던 "조건없는 UPDATE" 패턴으로 새 모듈에서 재발 — 심각도 높음

**상태**: ✅ FIXED — 두 함수 모두 `expected_status` 파라미터를 추가하고
105번 표준의 `conditional_update()`(`src/core/db/conditional_write.py`,
FND-01 이후 새 bounded context용으로 이미 존재하던 공용 헬퍼)로
전환. 호출부 4곳(submit.py::apply_fill, cancel.py, reconcile.py,
modify.py) 전부 갱신 직전에 읽은 `order.status`를 그대로 넘기도록
수정. 회귀 테스트 `test_update_from_exchange_raises_on_status_mismatch`로
확인(다른 경로가 먼저 CANCELLED로 바꾼 뒤 stale FILLED 갱신 시도 →
ConcurrencyConflictError, DB는 CANCELLED 유지). 전체 스위트 1003
passed로 직접 실행 검증.

**발견**: `src/services/order_service/repository.py::update_from_exchange()`(98-116행)와
`update_after_modify()`(119-134행) 둘 다 `UPDATE orders SET ... WHERE
order_id = $1`만 걸려 있고, **직전에 읽은 상태를 WHERE절에서 재확인하지
않는다** — 04/05/08/09/16/17번 항목에서 이미 확인·수정된 것과 정확히
같은 근본원인이 이번 세션에 새로 추가된 모듈에서 그대로 재발했다.
호출부 3곳(`cancel.py:44-45`, `submit.py:89-90` `apply_fill()`,
`reconcile.py:54-55` `resolve_unknown()`, `modify.py:56-57`)이 전부
먼저 SELECT로 상태를 Python에서 확인한 뒤, 이 조건없는 UPDATE로
덮어쓴다.

**재현 시나리오**: 주문 X가 SUBMITTED 상태. Watchdog가 안전정지 목적으로
`cancel_order(X)`를 호출해 거래소 취소 확인 후 status=CANCELLED로
쓰려는 순간, 동시에 체결통보/폴링 핸들러가 취소 직전에 거래소에서
이미 발생한 체결을 받아 `apply_fill(X, ...)`을 호출해 status=FILLED로
쓰려 한다. 두 UPDATE 모두 `order_id`만으로 무조건 통과하므로, Postgres에
나중에 커밋되는 쪽이 아무 충돌 감지 없이 조용히 이긴다 — 실제로는
체결됐는데 DB엔 CANCELLED로 남아 체결이 손익/리스크/포지션 계산에서
누락되거나, 반대로 실제로는 취소됐는데 FILLED로 남을 수 있다.
`resolve_unknown()` ↔ `cancel_order()`/`apply_fill()` 사이, `modify_order()`의
`update_after_modify` ↔ 다른 상태변경 호출 사이에도 동일 경합이
존재한다.

**권장 수정 방향**: 04/05/08/09/16/17번이 이미 쓴 패턴 그대로 —
`WHERE order_id=$1 AND status = $expected_prior` 조건을 추가하고
`RETURNING`이 빈 행이면 "동시 상태 변경 감지, 재조회 후 재판단"으로
연결. 이 프로젝트 안에 이미 5곳 넘게 정착된 패턴이 새 코드에 자동으로
적용되지 않은 것 자체가, 이 클래스의 버그를 코드리뷰 체크리스트나
린트 규칙으로 강제할 필요가 있다는 신호이기도 하다.

---

## 2026-09-02-21 · AlertService.evaluate_all_active()가 미검증 indicator/params로 인한 예외를 잡지 않아 전체 사용자의 알림 평가 루프를 영구 정지시킴 — 15번과 동일 클래스, 새 모듈에서 재발 — 심각도 높음

**상태**: ✅ FIXED — 2단 방어. (1) `alert_service.py::evaluate_all_active()`의
`self._indicators.calculate(...)` 호출을 try/except로 감싸 실패한
알림 하나만 건너뛰도록(모듈 docstring이 원래 약속한 동작) 수정.
(2) `main.py::_alert_evaluation_loop()`도 `evaluate_all_active()`
호출 자체를 try/except로 감싸 어떤 예외도 루프를 빠져나가지 못하게
2차 방어선 추가. 전체 스위트 1003 passed로 직접 실행 검증(전용
회귀 테스트는 추가하지 않음 — 기존 `test_alert_service.py`/
`test_alerts_router.py` 통과로 비회귀만 확인, 필요 시 후속 세션이
"잘못된 indicator 생성 → 다음 알림도 정상 평가됨" 케이스 추가 권장).

**발견**: `src/api/schemas/alerts.py::AlertCreateRequest.indicator`는
검증 없는 순수 `str`이고, `POST /alerts`(`src/api/routers/alerts.py::create_alert`)도
`AlertService.create_alert()`도 이 값을 `IndicatorService`가 실제로
지원하는 지표 집합과 대조하지 않는다. 배경 루프
`src/services/alert_service.py::evaluate_all_active()`(133-189행)는
자격증명 미등록만 `try/except CredentialNotFoundError`(143-149행)로
방어하고, 바로 다음 줄 `self._indicators.calculate(alert.indicator,
candles, **alert.params)`(151행)는 어떤 try/except로도 감싸여 있지
않다. `IndicatorService.calculate()`는 미지원 지표명에 `IndicatorError`를
던지고, 잘못된 `params` 키는 TA-Lib 호출에서 `TypeError`를 던진다.
호출부 `src/main.py::_alert_evaluation_loop()`(99-102행)도
`await alert_service.evaluate_all_active()` 자체를 try/except 없이
호출하고, 이 코루틴을 감싼 `asyncio.create_task(...)`(104행)는
종료 시점(`finally` 블록) 외에는 아무도 결과/예외를 회수하지 않는다.

**재현 시나리오**: 인증된 사용자 아무나 `POST /alerts`에
`{"indicator": "NOT_REAL", "operator": "<", "threshold": 1, ...}`처럼
존재하지 않는 지표명을 보내면 201로 그대로 생성되어 ACTIVE 상태로
저장된다. 다음 60초 평가 주기에 `evaluate_all_active()`가
`IndicatorError`를 던지고, 이 예외가 `_alert_evaluation_loop()`의
`while True` 루프 자체를 빠져나가 `alert_task` 코루틴을 영구히
죽인다 — **재시작 로직이 없어 프로세스를 재기동하기 전까지 그 어떤
사용자의 알림도 다시는 평가되지 않는다.** 이는
`alert_service.py`가 자기 docstring/모듈 설명에 명시한 보장("개별
알림 평가 실패는 그 알림만 건너뛰고... 다른 사용자의 알림 평가를
막으면 안 되므로 루프 전체를 실패시키지 않는다")과 정면으로 모순된다
— 정확히 15번 항목(EventBus의 audit_sink 실패가 워커 태스크를 조용히
죽임)과 같은 클래스의 버그가 새 모듈에서 재발한 것.

**권장 수정 방향**: (a) `evaluate_all_active()`의 `for alert in
alerts` 루프 안에서 `self._indicators.calculate(...)` 호출도
try/except로 감싸 실패한 알림 하나만 건너뛰도록 한다(모듈이 원래
약속한 동작). (b) `_alert_evaluation_loop()` 자체에도 15번 수정 때
적용한 것과 같은 원칙 — 넓은 try/except로 감싸 어떤 예외도 루프를
빠져나가지 못하게 하거나 태스크에 done-callback을 달아 죽는 즉시
로그+재기동. (c) 부수적으로 `AlertCreateRequest.indicator`를 생성
시점에 `IndicatorService`가 지원하는 이름 집합으로 검증해,애초에
잘못된 지표명이 저장되지 않도록 막는 것도 권장(사후 방어와 사전
검증 둘 다 필요 — 사전 검증만으로는 이미 저장된 레거시/악의적 데이터를
못 막고, 사후 방어만으로는 매 사이클 불필요한 실패를 반복함).

---

## 2026-09-02-22 · execution_loop의 fsm_state 갱신이 동시 tick에 대해 조건부가 아니라, 승인된 자본배분을 초과하는 중복 실주문 제출이 가능 — 심각도 높음(현재는 스케줄러 미배선으로 잠재적)

**상태**: ✅ FIXED — `tick.py::_make_fsm_state_writer()`가 반환하는
writer의 시그니처를 `(execution_id, new_state)` → `(execution_id,
expected_state, new_state)`로 바꾸고 105번 표준의 `conditional_update()`로
전환. 호출부 3곳(`run_execution_tick`의 메인 쓰기, `_handle_pending_fill_check`,
`executor.py::Executor.execute()`의 동기체결 전이) 전부 자신이 읽은
직전 fsm_state를 넘기도록 수정 — `run_execution_tick`은 충돌 시
RiskEngine 거부와 동일하게 조용히 이번 tick을 포기(다음 tick 재평가)하도록
`ConcurrencyConflictError`를 잡는다. 이걸로 두 tick이 동시에 같은
IDLE/HOLDING을 읽어도 조건부 쓰기에서 하나만 성공하고, 진 쪽은
`Executor.execute()`(실제 주문 제출)에 도달하지 못한다. 회귀 테스트
2개로 확인: `test_fsm_state_writer_raises_on_concurrent_state_change`
(writer 자체가 불일치 시 예외+무변경 검증), `test_concurrent_tick_race_only_submits_one_order`
(get_balance() 호출 시점에 "다른 tick"이 먼저 상태를 선점했다고
시뮬레이션 → `place_order_call_count == 0` 확인). 전체 스위트 1003
passed로 직접 실행 검증. **여전히 latent임은 변함없음** — 스케줄러
미배선이라 지금 당장 트리거되진 않지만, 배선 시점에 이미 안전한
상태로 준비됨.

**발견**: `src/services/execution_loop/tick.py::run_execution_tick()`이
`fsm_state`를 일반 `SELECT`(143/148행, 잠금 없음)로 한 번 읽고, 신호
평가·PortfolioEngine·RiskEngine을 거쳐 최종적으로 `_make_fsm_state_writer`
(75-84행)가 `UPDATE strategy_executions SET fsm_state = $2 WHERE id =
$1`로 **직전에 읽은 fsm_state를 재확인하지 않고** 조건없이 쓴다 — 이
프로젝트가 이미 같은 이유로 `SELECT ... FOR UPDATE`를 쓰고 있는
`portfolio_service.py`/`purchase_service.py`와 대조된다.

**재현 시나리오**: 같은 `execution_id`에 대해 두 번의 tick이 겹치면
(느린 거래소 응답으로 tick N이 끝나기 전에 tick N+1이 시작하거나,
스케줄러가 두 개 이상이면) 둘 다 `fsm_state=IDLE`을 읽고, 둘 다 매수
신호를 평가하고, 각자 독립적으로 `get_balance()`를 조회해 서로의
아직 미체결 주문을 못 본 채 PortfolioEngine/RiskEngine을 각각 통과한다.
`submit_order()`의 멱등키(`client_order_id`, `execution_id:state:
isoformat(now)`, `executor.py:87-89`)는 마이크로초 타임스탬프를
포함해 두 호출이 우연히 같은 키가 될 확률이 사실상 0이라 **19/20번의
멱등성 방어로도 이 중복을 못 잡는다** — 신호 하나에 실제 주문 두
개가 나가 `allocated_capital`을 초과한 포지션이 만들어질 수 있다.

**권장 수정 방향**: `run_execution_tick()`의 대상 실행 행을 `SELECT
... FOR UPDATE`로 잠그거나(advisory lock도 대안), 최소한 최종 쓰기를
`WHERE id=$1 AND fsm_state=$expected`(방금 읽은 값) 조건부로 바꿔
`RETURNING`이 빈 행이면 그 tick 자체를 건너뛰도록 해야 한다. **이
스케줄러가 아직 배선되지 않았다는 점이 유일한 안전장치이므로, 실제
scheduler 배선 PR과 반드시 같이 묶어서 처리할 것을 강하게 권고.**

---

## 2026-09-02-23 · 이번 감사에서 함께 발견한 중간 심각도 항목 4건 (execution_loop 2건 + 비동기 원자성 2건)

**상태**: 🟡 부분 수정 (a/c는 DevEngine 감사 세션이 직접 고침 — 관련
통합테스트 20개 통과, ruff/mypy strict clean. b/d는 아직 미착수, 아래
참조)

**a/c 수정 내용**: (a) `run_execution_tick()`에서 RiskEngine 승인 직후,
fsm_state를 PENDING류로 쓰기 **직전**에 `paused_by`를 DB에서 다시
한번 조회하도록 추가 — 신호 평가·RiskEngine 검사를 거치는 사이
Watchdog가 안전정지를 걸었다면 여기서 걸려 주문 제출로 이어지지
않는다(fsm_state를 아직 안 건드린 시점이라 되돌림도 필요 없음). (c)
`paused_by is not None` 조기 리턴을 PENDING-fill-check 분기
**뒤로** 옮겨, 정지된 실행이라도 이미 제출한 주문의 체결 확인은 계속
이뤄지도록 순서를 바꿨다. 새 테스트
`test_safety_pause_mid_tick_blocks_order_submission`(기존
`test_concurrent_tick_race_only_submits_one_order`와 동일한 주입
지점 — `get_balance()` 호출 시점에 pause를 걸어 재현),
`test_paused_execution_still_checks_pending_order_fill`(2틱 시나리오 —
1틱에서 주문 제출 후 정지, 2틱에서 체결 확인이 여전히 이뤄지는지
확인) 추가. (c) 테스트는 수정 전 코드로 되돌려 실제로 실패
(`order_status == 'SUBMITTED'`, FILLED로 못 감)하는 것까지 직접
확인. **b/d는 이번에 손대지 않음** — (b)는 주기적 크래시 복구
스캔이라는 별도 기능 신설이 필요해 빠른 수정 범위를 벗어남, (d)는
원문 자체가 이미 "비용 대비 지금 급하지 않음, 낮은 우선순위로 기록만"이라고
명시해둬 그대로 둠.

**a) tick.py의 pause 체크가 tick 시작 시점 1회뿐** —
`tick.py:145-146`이 `paused_by`를 tick 시작 시 한 번만 확인한다. 그
직후~Executor 호출(230행) 사이에 들어온 pause 요청은 이번 tick의
주문 제출을 막지 못한다. Watchdog가 이 사이 안전정지를 걸어도 이번
tick은 이미 진행 중인 주문 제출을 끝까지 마친다.

**b) Executor 호출 실패 시 fsm_state가 PENDING에 고아로 남을 수 있음** —
`tick.py:213-214`가 `fsm_state=PENDING`을 쓴 **다음에** `Executor.execute()`를
호출한다. 이 호출이 order 행 생성 전에 예외를 던지거나(파라미터
검증 실패 등) 프로세스가 죽으면, 실행은 order 없이 PENDING에 영구히
갇힌다 — 다음 tick의 `_handle_pending_fill_check`(98-106행)는 `order
is None`이면 조용히 리턴할 뿐 복구 로직이 없고, `watchdog_process.py`도
`fsm_state`는 건드리지 않는다. 틀린 주문이 나가는 건 아니지만(fail-safe
방향), 아무 자동 복구 없이 조용히 멈춘 실행이 방치된다.

**c) paused 상태의 실행은 미체결 주문의 fill 반영을 영원히 못 받음** —
`tick.py:145-146`의 `paused_by is not None` 체크가 150행의 PENDING
상태 체크보다 먼저 실행돼 조기 리턴한다. 즉 주문 제출 직후 일시정지된
실행은 그 주문이 실제로 체결됐는지 다시는 확인되지 않는다 —
`fsm_state`가 실제 거래소 상태와 영구히 어긋난 채로 남을 수 있다.

**d) order_service의 DB 쓰기와 이벤트 발행이 원자적이지 않음** —
`submit.py:55-64`/`cancel.py:47-56`/`reconcile.py:56-65` 모두 DB
커밋 후 `publish()`를 별도 단계로 호출한다(순서 자체는 올바름). 그
사이 예외나 프로세스 종료가 끼면 상태는 영속화됐지만 이벤트는 영원히
발행되지 않는다 — 데이터 손상은 아니고 이벤트 유실이라 심각도는
낮지만, 이벤트를 구독하는 다른 서비스(알림 등)가 그 변화를 영원히
모르게 된다.

**권장 수정 방향**: (a) Executor 호출 직전에 pause 상태를 한 번 더
확인. (b) tick이 크래시 복구 스캔(예: PENDING인데 N분 넘게 order가
없는 실행을 주기적으로 찾아 IDLE로 되돌리거나 알림)을 갖추도록 보강.
(c) paused 체크와 PENDING-fill-check 체크의 순서를 바꾸거나, paused여도
fill 확인만은 계속 수행하도록 분리. (d) outbox 패턴(같은 트랜잭션에
이벤트를 임시 테이블로 같이 쓰고 별도 워커가 발행) 도입 여부는
비용 대비 지금 급하지 않음 — 낮은 우선순위로 기록만.

---

## 2026-09-02-24 · Alert 생성에 사용자당 개수 상한이 없음 (참고용, 우선순위 낮음)

**상태**: ✅ FIXED — DevEngine 감사 세션이 직접 고침(관련 통합테스트
9개 통과, ruff/mypy strict clean)

**수정 내용**: `alert_service.py`에 `MAX_ACTIVE_ALERTS_PER_USER = 50`
Draft 상수를 추가하고, `create_alert()`가 INSERT 전에 해당 사용자의
`status = 'ACTIVE'` 알림 개수를 세어 상한 이상이면 `AlertError`를
던지도록 수정. 새 테스트
`test_create_alert_rejects_over_per_user_cap`(monkeypatch로 상한을
2로 낮춰 재현) 추가.

**발견**: `AlertService.create_alert()`(`alert_service.py:82-111행`)에
사용자당 활성 알림 개수 제한이 없다. `evaluate_all_active()`가 전체
알림을 순차 `for` 루프로 도는 구조라, 한 사용자가 알림을 대량 생성하면
그 사용자 몫만큼 매 평가 주기(60초)의 처리 시간이 늘어나 다른 모든
사용자의 알림 평가도 함께 지연된다.

**권장 수정 방향**: `create_alert()`에 사용자별 ACTIVE 알림 개수
상한(예: 20~50개)을 추가.

---

## 2026-09-01-08 · PAPER 실행 루프가 adapter의 실제 sandbox 상태를 증명하지 않음

**상태**: 🟢 두 세션이 독립적으로 수정한 결과가 병합됨(이 세션 자체
확인 — `is_sandboxed`/`is_paper_trading` 두 테스트 모두 포함해 관련
통합테스트 통과, ruff/mypy clean). 감사 세션 재검증 대기.

**수정 내용**: 서로 다른 두 세션이 같은 발견을 각자 독립적으로 막았고,
병합 결과 두 검사가 모두 남아 상호 보완한다.

1. (이 세션) `ExchangeAdapter`에 `is_sandboxed: bool` 추상 프로퍼티를
   신설. `BitgetAdapter.is_sandboxed`는 생성자의 `demo_mode`를,
   `KISAdapter.is_sandboxed`는 `is_paper_trading`을 그대로 노출한다(둘 다
   paper 헤더/모의투자 base URL 전환과 동일 조건 — 새 상태를 만들지 않고
   이미 있던 진실의 원천을 노출만 함).
2. (`d9548c1`, PR #7) `ExchangeAdapter.is_paper_trading`이 false인
   adapter를 별도로 거부하는 code-level fail-closed guard.

`Executor.execute()`는 이제 `mode == 'PAPER'`, `adapter.is_sandboxed`,
`adapter.is_paper_trading` 세 가지를 모두 확인해, 하나라도 아니면
`FrozenZoneLiveModeBlockedError`/`FrozenZonePaperAdapterBlockedError`를
던진다 — DB의 mode 문자열과 실제 adapter 상태가 반드시 다 일치해야
통과한다.

**새 테스트**(둘 다 유지, `tests/integration/test_executor.py`):
`test_paper_mode_with_non_sandboxed_adapter_is_hard_blocked`(
`is_sandboxed=False` 차단 확인) +
`test_paper_mode_rejects_a_live_configured_adapter_before_order_submission`(
`is_paper_trading=False` 차단 확인).

**남은 축소(정직하게 명시)**: 이 수정은 "adapter 객체 스스로가 보고하는
값"을 신뢰한다 — `CredentialResolver`가 여전히 유일한 adapter 생성
경로(`main.py`에서 override 없이 기본값 True로 생성)라 지금은 안전하지만,
egress allowlist·credential provenance 검증까지 포함한 완전한 attestation
체계는 이 리프의 스콥이 아니다. 실계정 도입 시점에 별도 ADR·owner
review로 분리해야 한다 — 이 판단은 코덱스 문서군의
`103_enterprise_architecture_full_audit_and_remediation_brief_v1.0.md`
RT-02(PAPER→LIVE egress 우회, P0)와 정확히 같은 결론이다: 코드 플래그
수준의 격리는 배포/네트워크 수준 격리가 아니다.

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

**남은 조치**: `is_paper_trading`은 adapter configuration의 code-level
assertion이다. execution plane은 다음 단계에서 sandbox endpoint, sandbox
credential provenance, egress allowlist를 deployment 수준에서도 검증해야 한다.
특히 실제 network egress를 막는 integration test를 추가하고, 기존 LIVE path의
수정·활성화는 별도 ADR·owner review·release approval로 분리한다.

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

**후속 개선(2026-09-02, 구현 세션)**: 사용자가 직접 "2차인증에 문제가
있었다"고 보고해 재조사한 결과, 이 재사용 방지 자체는 정확히 의도대로
동작하지만 `MfaService.verify()`가 "코드 재사용" 실패와 "코드 자체가
틀림" 실패에 똑같이 `"인증 코드가 올바르지 않습니다"`를 던지고
있었다 — 실제 사용자 시나리오(예: 응답 지연/일시적 DB 장애로 첫
`/auth/mfa/verify` 요청이 실패한 줄 알고 인증 앱에 아직 떠 있는 같은
코드로 재시도)에서는 코드가 틀린 게 아니라 "이미 성공 처리된 코드"인데
"코드가 틀렸다"는 메시지를 받아 2단계 인증 자체가 고장난 것처럼
보인다. `/auth/mfa/verify`는 `get_current_user`(Bearer 토큰)로 이미
인증된 사용자만 호출하는 엔드포인트라(#12의 로그인 타이밍
사이드채널과 무관 — 로그인/재인증 경로의 메시지는 그대로 뭉뚱그려
둠) 재사용 여부를 구분해 알려줘도 계정 정보가 새어나가지 않는다. 이제
재사용으로 막힌 경우엔 `"이미 사용한 코드입니다. 인증 앱에 새로
표시되는 코드로 다시 시도해주세요."`를 던진다. 기존 테스트 2개에
메시지 검증(`pytest.raises(MfaError, match=...)`)을 추가해 두 실패
경로가 서로 다른 메시지임을 고정했다(관련 테스트 6개 통과,
ruff/mypy strict clean).

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
