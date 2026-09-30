# 인증·RLS 적대적 감사 — task-9420

날짜: 2026-09-30. 기준 커밋: `4e5c4d409ba1e9775bf6ed7d7aac1a423dbd8baa`.
근거: ADR-2026-09-26-C Decision 3, L4_platform_observability_tenancy_api_v1.0
§2 M3/M5·§3.4·§9 PLT-22/23/24/30·§10 리스크1, INVARIANTS I-10.
범위: services/auth 전 파일, auth_service, auth router, deps, 두 지정 RLS
마이그레이션과 8개 적용 테이블 및 해당 repository. 접합면으로 foundation_deps,
MFA 로그인 콜백, WebSocket 인증도 확인했다. 프로덕션·테스트 코드 변경 없음.
정정 리프 발행은 CTO/PM 소관이다.

## 0. 요약

| 발견 | 심각도 | 결론 | 증거 |
|---|---|---|---|
| F1 | M | FORCE RLS 테이블의 여러 실제 repository에 GUC 바인딩이 없어 정상 테넌트 행도 조회되지 않는다 | 실DB 앱 역할에서 consent 조회 None, 동일 역할·명시 바인딩에서는 1행 |
| F2 | M | 진행 중 로그인 성공이 나중에 설정된 잠금을 무조건 해제한다 | 성공 경로 barrier 뒤 실패 5회 주입: locked=True → 토큰 발급, counter=0, lock=NULL |
| F3 | M | 서명된 JWT의 sub/tid/auth_level과 sid가 가리키는 세션의 일관성을 검사하지 않는다 | A 세션 + B sub + A tid + MFA_VERIFIED 토큰이 B 사용자로 수용됨 |
| F4 | M | refresh의 활성 확인과 회전 사이 logout이 완료되어도 폐기된 세션을 회전하고 토큰을 반환한다 | 읽기 뒤 다른 연결에서 revoke: issued=True, revoked=True, rotated=True |
| F5 | L | refresh 재사용 감지 시 명세의 auth.refresh_reuse_detected 감사 이벤트를 기록하지 않는다 | 재사용 처리 호출 그래프 정적 확인 및 기존 재사용 테스트 통과 |

**S 0건 / M 4건 / L 1건. 발견 0건이 아니므로 도메인 통과 불가.**
S는 외부 호출자가 추가 신뢰 경계 파괴 없이 즉시 타 테넌트 접근/권한 우회를
실행할 수 있는 경우로 보았다. F3은 유효한 서명을 가진 불일치 claims를 주입한
방어심층 실험이며 서명 위조나 일반 사용자의 타 계정 접근을 증명하지 않는다.
F4도 폐기를 되돌리지 않으므로 발급 토큰의 API 사용 성공을 주장하지 않는다.
F1은 격리 우회가 아니라 배선·가용성 결함이다. 실제 운영 DSN 권한은 조사하지 않았다.

## 1. 선례·계약 대조

| 안전 경로의 선례/계약 | 인증·RLS 관찰 | 판정 |
|---|---|---|
| risk_gate/application/evaluate_risk_gate.py:121은 connection.tenant_id != tenant_id 거부 | deps.py:93은 sid 활성 여부만 검사하고 sub/tid와 비교하지 않음 | F3 |
| mandates/adapters/postgres_repository.py의 activate_revision은 관찰한 active_revision_id로 CAS | auth_service.py:251/260 성공 UPDATE는 user_id 조건만 사용 | F2 |
| connections/adapters/postgres_repository.py:107/117/150은 tenant_transaction + 명시 tenant 조건 | trust 및 mandates/paper_control/reconciliation/risk_gate/evidence의 다수 경로는 pool.acquire만 사용 | F1 |
| session_repository의 refresh_hash CAS로 동시 재사용 시 승자 1명, 최종 revoke | 같은 CAS에 revoked_at/expiry 조건이 없어 logout과의 경합을 직렬화하지 못함 | F4 |
| L4 §3.4는 refresh 재사용 시 세션 revoke + 명명된 감사 이벤트 요구 | revoke_reason만 갱신하며 감사 이벤트 호출 없음 | F5 |
| INVARIANTS I-10: 존재가 아니라 실제 배선 증명 | RLS 정책/플래그 검증은 통과해도 repository 호출은 정상 행을 잃음 | F1 |

개인 테넌트의 tenant_id=user_id 발급 자체는 signup의 PERSONAL tenant 원자 생성과
일치한다. organization 선택은 resolve_tenant_context의 membership 검사 경로이며,
단순히 로그인에서 user_id를 쓰는 것을 테넌트 혼동으로 판정하지 않았다.
foundation_deps.py:231-248은 MFA 최근성 15분을 서버의 mfa_verified_at으로 계산한다.
따라서 JWT auth_level만으로 모든 민감 경로의 MFA가 우회된다는 주장은 하지 않는다.
레거시 orders/positions/strategy_executions의 RLS 비활성은 §10의 명시된 이관 결정이라
신규 발견으로 세지 않았다. FROZEN_PAPER_ONLY 수정 없음.

## 2. 발견과 재현

### F1 — 실제 repository의 RLS 바인딩 누락 (M)

위치: src/foundation/trust/adapters/postgres_repository.py:76/86/96/106,
mandates/adapters/postgres_repository.py:74,
paper_control/adapters/postgres_repository.py:80/87,
reconciliation/adapters/postgres_repository.py:62,
risk_gate/adapters/postgres_repository.py:220,
evidence/adapters/postgres_repository.py:50/172.
각 경로는 전달된 pool에서 새 연결을 빌리며 tenant_transaction/system_transaction을
쓰지 않는다. foundation_deps는 repository를 같은 일반 pool로 구성하고, main의 pool
생성도 요청별 tenant GUC를 주입하지 않는다. 일부 connection 경로만 이미 이관됐다.

재현(테스트 DB, 앱 역할): 기존 `_seed_consent` 또는 테스트가 만든 ACTIVE consent를
고른다. pool.acquire를 감싼 context manager에서 conn.transaction과
`SET LOCAL ROLE aios_app`만 적용하고, 원래 PostgresTrustRepository의
get_active_consent(tenant_id,purpose)를 호출한다. 결과 None. 같은 역할에서
`SELECT set_config('app.tenant_id', $1, true)` 후 같은 행 조회는 1행이다.
이는 권한 상승이나 정책 완화 없이 재현됐다. tenant 조건이 SQL에 있어도 GUC가 비면
RLS가 거부한다. 쓰기 역시 정책 위반 가능성이 있으나 이번 동적 증거는 조회에 한정한다.

기존 test_rls_foundation.py는 AppRoleTx에 tenant를 직접 넣어 SQL을 실행한다.
따라서 이 테스트가 녹색이어도 실제 repository 바인딩을 증명하지 않는다.
후속: 8개 컨텍스트의 read/write/system 경로를 명시 tenant 계약으로 이관하고 실제
repository를 aios_app로 호출하는 양성·교차테넌트·무바인딩 음성 테스트를 추가한다.
운영 DSN을 슈퍼유저로 바꾸거나 FORCE를 제거하는 해결은 금지한다.

### F2 — 성공 로그인의 잠금 해제 TOCTOU (M)

위치: src/services/auth_service.py:187-218, 241-269;
src/services/auth/login.py:99-100. 실패 카운터 UPDATE 자체는 원자적이지만 성공 UPDATE는
최초 SELECT 이후의 locked_until/status 변경을 조건으로 확인하지 않는다.

실행한 결정론적 스케줄:
1. 새 사용자로 login.login을 시작한다. auth_service.record_audit_log를 메모리에서
   감싸 action_type=auth.login_success일 때 asyncio.Event barrier에서 정지한다.
2. 별도 pool 연결에서 lockout.register_failed_attempt를 같은 user_id로 5회 호출한다.
   SELECT locked_until > now() 결과 True를 확인한다.
3. barrier를 해제한다. 원래 감사 함수와 원래 성공 UPDATE가 실행된다.
4. 로그인은 TokenPairResponse를 반환하고, failed_login_attempts=0/locked_until=NULL이다.

원래 비밀번호는 올바르므로 무자격 로그인을 증명한 것은 아니다. 잠금 판정 이후의
성공 요청이 최신 잠금을 취소하는 정책 경쟁이며, 정지/삭제 상태와 성공 UPDATE 사이도
같은 재검증 부재 구조다(후자는 이번 동적 실험 범위 밖).
기존 test_lockout_atomic.py의 concurrent_failed_logins 및 successful_login_resets는
각각 실패끼리 경합/순차 성공만 검증한다. 혼합 경합이 빠져 있다.
후속: 성공·실패·잠금·계정 상태 변경의 직렬화 계약을 정의하고 최종 상태 조건부 갱신,
발급 직전 재검증 및 혼합 동시성 테스트를 정정 리프에 포함한다.

### F3 — JWT/세션 principal 결합 검증 누락 (M)

위치: src/api/deps.py:78-99, src/services/auth/session_repository.py:86-90.
접합면 src/api/ws/market_ws_dispatch.py:104-118도 동일하게 sid의 존재만 확인한 뒤
claims.tid/sub를 반환한다.

재현: AuthService.signup으로 A/B를 만든 뒤 issue_token_pair로 A 세션을 생성한다.
테스트 전용 TokenIssuer와 TokenVerifier(동일 임시 키)를 사용해 다음을 실행했다.

```python
mixed = issuer.issue_access(
    user_id=b.user_id, tenant_id=a.user_id,
    session_id=pair_a.session_id, auth_level="MFA_VERIFIED",
)
accepted = await get_current_user(mixed, pool, verifier)
assert accepted.user_id == b.user_id
assert accepted.auth_level == "MFA_VERIFIED"
```

현재 결과는 수용이다. 정상 발급 경로가 이런 JWT를 발급한다는 증거는 없으며,
외부 공격자가 키 없이 재현할 수 있다는 뜻도 아니다. 이미 서명 검증을 통과한 값이라도
DB 세션 소유자/tenant/인증수준과 대조하지 않는 계약 공백이다.
기존 test_tokens.py는 서명·알고리즘·스키마 변조를 검사하고 test_sessions.py는 세션
CRUD를 검사한다. 두 경계를 잇는 불일치 검증은 이들 테스트가 보장하지 않는다.
후속: REST/WS 공통 세션 검증에서 sub/tid 및 auth_level 권위의 일관성을 검증하고,
A/B 교차 조합을 거부하는 테스트를 추가한다. membership 기반 tenant 선택과 JWT
발급 tenant의 계약도 명시해 정당한 organization 전환을 깨뜨리지 않아야 한다.

### F4 — logout 완료 후 refresh 회전/발급 (M)

위치: src/services/auth/refresh.py:53-80;
src/services/auth/session_repository.py:93-115.
get_active와 rotate_refresh가 별도 문장이며 회전 WHERE 조건은 id+refresh_hash뿐이다.

재현: 메모리 patch로 get_active의 원래 결과를 읽은 직후 별도 연결에서
session_repository.revoke(session_id,reason='audit-concurrent-logout')를 완료한 다음
기존 읽기 결과를 반환한다. 정상 refresh 토큰으로 refresh.refresh를 호출했다.
TokenPairResponse가 반환됐고 DB의 revoked_at과 rotated_at 모두 NOT NULL이었다.
**폐기 상태는 유지된다.** 후속 REST 요청은 get_active에서 거부되므로 세션 부활이나
logout 후 API 사용 성공으로 과장하지 않는다. 이미 거부해야 할 회전에 성공 응답과
사용 불가능한 새 토큰을 주는 경쟁 상태다. 만료 검사와 회전 사이에도 같은 시간 창이 있다.

기존 test_concurrent_refresh_with_same_token_revokes_session_fail_closed는 같은
토큰끼리의 경쟁이며 logout/expiry와의 경쟁을 검사하지 않는다.
후속: 회전 UPDATE에 활성·만료 조건을 포함하고 실패 원인을 재사용과 구별한다.
logout을 회전 직전에 완료시키는 barrier 테스트에서 발급 거부를 검증한다.

### F5 — refresh 재사용 감사 이벤트 누락 (L)

위치: src/services/auth/session_repository.py:109-115,
src/services/auth/refresh.py:65-79; 계약: L4 §3.4 refresh 행.
재사용 감지는 revoke_reason='refresh_reuse'만 갱신하고 RefreshReuseDetected를 던진다.
해당 호출 그래프에 감사 기록이 없으며 src 전체에도 auth.refresh_reuse_detected가 없다.
일반 HTTP 에러 로깅은 명세의 명명된 사용자/세션 보안 감사 이벤트와 동일하지 않다.
기존 test_refresh_reuse_of_old_refresh_token_revokes_session / test_rotate_refresh_reuse_of_old_hash_revokes_session은
revoke와 예외만 검증한다. 이번 기존 테스트 실행에서도 이 경로는 녹색이었다.
후속: 세션 폐기를 유지하면서 actor/session/tenant와 재사용 사유를 감사 기록에 남기고,
토큰 평문 미기록·감사 백엔드 실패·동시 재사용의 이벤트 수 계약을 검증한다.
정적 발견이며 별도 감사 저장소 장애 주입 실험을 수행했다는 주장은 하지 않는다.

## 3. RLS 8개 테이블 전수 확인

두 마이그레이션의 목록 일치, 실제 pg_class의 ENABLE/FORCE, pg_policies의
USING/WITH CHECK 동일성을 확인했다. 모든 쿼리는 비슈퍼유저·NOBYPASSRLS 앱 역할로
강등해 실행했다. 슈퍼유저 연결은 seed/catalog 용도다.

| 테이블 | ENABLE/FORCE | 실제 행을 둔 격리 확인 | 실제 repository 바인딩 |
|---|---|---|---|
| consent_record | True/True | 기존 테스트: 자기 행만, 타 tenant INSERT/UPDATE 거부, DELETE 0 | F1 실DB 재현 |
| account_connection | True/True | batch_a: A/B 행 seed, 교차 SELECT 0 | 일부 경로만 tenant_transaction |
| risk_evaluation | True/True | batch_a: A/B 행 seed, 교차 SELECT 0 | 일반 acquire 경로 존재 |
| paper_deployment | True/True | 추가 관측: A/B seed, 자기 행 존재·타 행 0·무바인딩 0 | 일반 acquire |
| reconciliation_run | True/True | 추가 관측: A/B seed, 자기 행 존재·타 행 0·무바인딩 0 | 일반 acquire |
| valuation_snapshot | True/True | 추가 관측: A/B seed, 자기 행 존재·타 행 0·무바인딩 0 | paper_input_adapter.py:17에서 미사용 명시; 이 표로 배선 결함 판정 안 함 |
| portfolio_mandate | True/True | 추가 관측: A/B seed, 자기 행 존재·타 행 0·무바인딩 0 | 일반 acquire |
| foundation_audit_event | True/True | 기존 테스트: tenant 행 격리, system은 NULL 행만 | 일반 acquire, system 바인딩 누락 |

추가 관측은 tests.foundation.integration.paper_control.conftest의
`tenant_with_mandate(pool,mandate_repo,trust_repo)`와 `request(...)`를 두 번 호출해
실제 mandate/deployment를 생성했다. 같은 두 tenant로 reconciliation_run에
COMPLETED 행, valuation_snapshot에 PAPER/ESTIMATED 행을 각각 넣었다.
각 테이블에서 SET LOCAL ROLE aios_app + app.tenant_id=A 후 WHERE 없는 SELECT 결과가
비어 있지 않고 tenant 집합이 {A}인지 단언했다. tenant=B 조건 조회와 빈 GUC 조회는
각각 0행이었다. 빈 테이블의 0행을 격리 증거로 사용하지 않았다.

8개 전부의 INSERT/UPDATE/DELETE 및 모든 자식 테이블까지 동적 전수 검증한 것은 아니다.
일반 행 predicate의 쓰기 방어는 consent의 실제 음성 테스트, 모든 테이블의 동일
USING/WITH CHECK 정책 확인으로 보완했다. 부모 FK만으로 자식 SELECT가 자동 격리된다고
간주하지 않았으며, 두 마이그레이션 밖 자식 테이블은 이번 전수표의 증명 범위가 아니다.

## 4. 검증 기록과 한계

Python: C:/aios/mihwa-aios/.venv/Scripts/python.exe.
DB: 전용 aios_test_codex_impl_1. 비밀값·JWT·refresh 평문 출력 없음.
신규 테스트 파일 없이 기존 테스트와 stdin 일회성 Python probe를 실행했다.

| 검증 | 결과 |
|---|---|
| unit/services/auth/test_tokens + integration/services/auth/{test_login_refresh_logout,test_lockout_atomic} + integration/auth/test_sessions + core/db/test_rls_{foundation,force_foundation,foundation_fa0a_batch_a,bypass_denied} | 108 passed, 22.46s |
| integration/test_auth_service + test_auth_router + test_auth_router_mfa + adversarial/auth/test_signup_tenant_atomicity + integration/auth/test_signup_concurrent_email | 47 passed, 60.66s |
| ruff check src tests scripts | 통과 |
| mypy src | 1565 source files 통과 |
| check_zone_manifest.py | 통과 |
| python -m scripts.replay_verify (전용 DATABASE_URL) | OK, streams=0 |
| 별도 실제 함수/DB probe | F1/F2/F3/F4 재현, 추가 4개 테이블 A/B 행 격리 단언 통과 |

음성 ≥3: 알 수 없는 세션·중복 refresh hash·잘못된 auth level·교차 tenant 쓰기·RLS
DDL 약화 거부를 기존 테스트에서 실행했다. 실패 주입/적색 재현은 기존 FORCE 제거 후
AssertionError/rollback, GUC 바인딩 실패, CAS 제거 시 동시 재사용 성공 테스트로 확인했다.
성능은 기존 refresh/RLS p95 50ms 및 lockout 자체 예산 단언을 실행해 통과했다.
측정 p95 원값은 테스트가 출력하지 않으므로 임의 수치로 채우지 않는다.
두 warning은 SHA384 음성 테스트의 키 길이 경고와 Starlette deprecated status alias다.

replay_verify는 주문/원장 stream 검증 도구이고 이번 DB에 대상 stream이 0개였다.
명령 통과는 기록하지만 인증/RLS 재생 검증이나 비어 있지 않은 D3 replay 증거로 세지 않는다.
인증/RLS의 event replay 자체는 N/A(해당 도구의 projection 대상 아님).
이번 감사의 완료는 발견 기록·전달이며 발견이 수정되거나 안전축 D3가 달성됐다는 뜻이 아니다.
발견별 정정 및 재감사 후 ADR의 연속 0건 조건을 별도로 판정해야 한다.
