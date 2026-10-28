# ADR-2026-10-28-A — 공급망 보안 범프 처리·기한부 예외 정책

- 상태: Accepted (2026-10-28, 사용자 일괄 승인 — 초안은 Proposed, 본문은 CTO가 "예외는 우회" 지적을 반영해 개정)
- 출처: 2026-10-24~28 lock 수정 3회·CI 적색 반복 실측, scripts/run_pip_audit.py, .pip-audit-ignore
- 관련: ADR-2026-09-09-B(H-5 공급망 게이트), ADR-2026-10-23-A(보안 범프 허용, patch/minor 채택), ADR-2026-10-15-A(수렴 모드)

## 배경

2026-10-24~28 기간 중 `requirements-lock.txt`와 `.pip-audit-ignore`가 3회 수정되었고, 매번 CI 적색 → 정정 리프 → 재수정 → 재적색이 반복됐다.

1. filelock/setuptools: 수정판이 Requires-Python >=3.11이라 3.10 런타임에 설치 불가 → 3.10 호환 보안 버전 고정(commit 7dfb3f3ed).
2. msgpack CVE-2026-114613: 1.2.2 → 1.2.3 패치(commit 0a895f1ec). scratch venv(py3.10) freeze 비교로 차이 1줄 확인.
3. dnspython CVE-2026-114605: 수정판 2.9.0이 Python>=3.11 전용이라 3.10에 설치 불가. 임시로 기한부 예외(만료 2026-11-27, commit 2ea5b6b94)를 등록했으나,
   사용자가 "예외는 우회에 해당한다"고 지적했다. 그 지적이 옳아 **근본 제거**를 택했다: dnspython의 유일한 요구자는
   `email-validator`였고 앱은 pydantic `EmailStr`(DNS 조회 없음) 두 곳에서만 썼다. `EmailStr`을 내장 검증으로 교체(3e5cfd239)하고
   `email-validator`를 의존성에서 제거(be87227b2)해 취약 패키지가 트리에서 사라졌고 예외도 삭제됐다. full CI supply_chain 통과.

## 결정

### (a) 처리 우선순위 — 예외보다 제거·범프가 먼저다

1. **범프**: patch/minor이고 3.10에서 설치 가능한 수정판이 있으면 범프한다.
2. **제거**: 수정판이 3.10에 없으면 먼저 의존 자체를 없앨 수 있는지 본다(요구자 추적: `pip show`의 Required-by, 코드 사용처 grep).
3. **기한부 예외**: 1·2가 모두 불가능하고 도달 불가가 입증될 때만 마지막 수단으로 쓴다. 예외는 게이트 완화이므로 사용자 승인 없이 등록하지 않는다.
4. **major**: 호환성 파급이 있어 CTO 승인 + 로컬 전체 게이트 녹색이 필요하다.

### (b) 범프 절차 — 자동화 범위

- full CI의 supply_chain 적색은 `ci_converge.maybe_open_bump_leaf`가 patch/minor 범프 리프(claude-local 우선)를 자동 발행한다(CVE당 열린 리프 1개).
- 워커는 임시 venv(py3.10)에 기존 lock을 설치하고 대상만 올려 `pip check`, `scripts/run_pip_audit.py` exit 0, 스모크 테스트를 통과시킨 뒤 lock을 공식 재생성 절차로 갱신한다.
- 손편집 금지: lock의 각 줄은 `pip freeze` 출력이다. 버전 숫자를 직접 고치면 의존성 트리가 깨진다.
- major, `.pip-audit-ignore` 수정, 3.10 미지원 수정판은 워커가 커밋하지 않고 needs_decision으로 올린다.

### (c) 기한부 예외 형식 (마지막 수단일 때만)

`.pip-audit-ignore` 항목은 다음을 모두 가져야 한다.

1. **id**: CVE/GHSA 식별자
2. **reason**: 도달 불가 근거(어떤 경로로 의존하는지, 앱이 해당 기능을 쓰지 않는 이유)와 사용자 승인 일자
3. **expires**: 등록일 기준 30일 이내

만료된 예외가 있으면 `run_pip_audit.py`가 pip-audit 실행 전에 exit 1로 실패한다. 만료 7일 전 healthcheck가 `pip_audit_waiver_expiring`으로 경보한다.
CI는 예외를 자동 등록하거나 자동 연장하지 않는다.

## 대안: Python 3.11+ 이전

3.11 이상으로 `requires-python`을 올리면 filelock/setuptools 같은 3.11 전용 수정판을 쓸 수 있다. 그러나 전체 개발 환경·CI·운영 런타임 재검토가 필요하므로
이 ADR의 범위 밖이며, 별도 프로젝트로 다룬다. 그 전까지는 위 (a) 순서로 대응한다.

## 결과/위험

- 현재 `.pip-audit-ignore`에 활성 예외는 없다(dnspython 예외는 근본 제거로 삭제됨).
- 위험: lock 재생성 중 의존성 충돌 → 임시 venv 설치 실패. 이 경우 major로 분류하고 CTO에 에스컬레이션한다.
- 재평가: 새 예외를 등록할 때마다 이 ADR의 (a) 순서를 지켰는지 reason에 적는다.

## 근거

- `scripts/run_pip_audit.py`: 래퍼 동작, 예외 파싱·만료 검사
- commit 3e5cfd239, be87227b2: EmailStr 교체, email-validator·dnspython 제거, 예외 삭제
- ci_converge.maybe_open_bump_leaf (pm 저장소): 범프 리프 자동 발행
- ADR-2026-10-23-A 결정 1′: 보안 범프(pip-audit 적발)는 로컬 워커 자동 허용(패치·마이너)
