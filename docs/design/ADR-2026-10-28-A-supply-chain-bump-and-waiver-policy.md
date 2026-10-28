# ADR-2026-10-28-A — 공급망 보안 범프 자동 제안·기한부 예외 정책

- 상태: Proposed (2026-10-28, CTO 판단 대기 — 사용자 승인 전)
- 출처: 2026-10-24~28 lock 수정 3회·CI 적색 반복 실측, scripts/run_pip_audit.py, .pip-audit-ignore
- 관련: ADR-2026-09-09-B(H-5 공급망 게이트), ADR-2026-10-23-A(보안 범프 허용, patch/minor 채택), ADR-2026-10-15-A(수렴 모드)

## 배경

2026-10-24~28 기간 중 `requirements-lock.txt`와 `.pip-audit-ignore`가 3회 수정되었고, 매번 CI 적색 → 정정 리프 발행 → 재수정 → 재적색의 반복이 발생했다.

구체적 사건:

1. **dnspython CVE-2026-114605** (2026-10-25, commit 7dfb3f3ed): Python 3.10 호환 보안 버전 고정. filelock 3.20.3, setuptools 84.1.0이 Requires-Python >=3.10이라 3.10 런타임에 설치 불가. filelock 4.2.1은 >=3.11이라 제외. 3.20.3은 3.20.4에서 도입된 취약 기능 이전의 보안 수정 버전.
2. **msgpack CVE-2026-114613** (2026-10-28, commit 0a895f1ec): msgpack 1.2.2 → 1.2.3 패치. scratch venv(py3.10)에 기존 lock 설치 후 msgpack만 올려 freeze 비교 — 차이는 msgpack 한 줄뿐. dnspython 2.8.0(CVE-2026-114605)은 수정판 2.9.0이 Python>=3.11 필요해 범위 밖.
3. **.pip-audit-ignore 기한부 예외** (2026-10-28, commit 2ea5b6b94): dnspython 2.8.0의 CVE-2026-114605에 예외 등록(만료 2026-11-27). pip index 실측: 3.10 최신 2.8.0, 2.9.0은 Python>=3.11 전용. 도달 불가 근거: dnspython은 email-validator의 전이 의존성뿐이고 앱은 pydantic EmailStr(check_deliverability=False, DNS 조회 없음)만 쓰며 src에서 dns 모듈을 직접 호출하지 않는다.

이 과정에서 `run_pip_audit.py`의 동작을 정확히 이해해야 했다:

- `--strict`와 `--skip-editable`을 같이 쓰면 이 저장소에서는 항상 적색(자기 자신이 SkippedDependency로 리포트됨). 그래서 `--format json`으로 원시 결과를 받아 스크립트가 strict 판정을 대신한다(run_pip_audit.py, L1-26).
- skip_reason이 정확히 "distribution marked as editable"인 항목만 통과, 그 외 모든 skip은 fatal(L13-15).
- `.pip-audit-ignore`는 사유+만료일을 함께 적게 하고, 만료된 예외가 있으면 pip-audit 실행 전에도 실패(L16-19).
- lock 파일에 핀 있는 패키지의 취약점은 사람이 lock을 재생성해야 고친다. lock 밖(local-only) 패키지는 Actions parity 밖 문제(L272-294).

## 결정안

### (a) 보안 범프 처리 절차

| 범위 | 처리 주체 | lock 재생성 | 머지 조건 |
|---|---|---|---|
| patch(수정판 존재, 3.10 호환) | `propose_security_bumps.py` 자동 제안 → 사람 | scratch venv(py3.10)에 기존 lock 설치 후 대상만 올려 `pip freeze` 비교. 손편집 금지 | 로컬 전체 게이트(ruff, mypy, zone_manifest, pytest) 녹색 |
| minor(수정판 존재, 3.10 호환) | 동일 | 동일 | 동일 |
| major(호환성 파급) | 수동 검토. 아키텍처 결정 필요 | — | CTO 승인 + 로컬 전체 게이트 녹색 |

`run_pip_audit.py`가 lock 파일 패키지의 취약점을 보고하면(출력: "위 N건은 requirements-lock.txt에 핀이 있다 — 재생성하고 이 스크립트가 exit 0임을 확인하라"), 사람은 scratch venv에서 lock을 재생성한다. `propose_security_bumps.py`는 이 절차를 안내하는 제안 스크립트(아직 구현되지 않음).

**손편집 금지**: `requirements-lock.txt`의 각 라인은 `pip freeze` 출력이므로, 수동으로 버전 번호를 고치면 의존성 트리가 깨질 수 있다. 반드시 scratch venv에서 `pip install -e "C:\aios\aios[dev,test]"` → `pip freeze`로 재생성한다(L13-18).

### (b) 기한부 예외 정책

`.pip-audit-ignore`에 다음 3항목이 모두 있으면 기한부 예외로 인정한다:

1. **id**: CVE-XXXX-XXXXXX 형식
2. **reason**: 도달 불가 근거(어떤 경유로 의존하는지, 앱이 실제로 해당 기능을 쓰지 않는지)
3. **expires**: 만료일(YYYY-MM-DD). 현재 날짜부터 ≤30일

만료된 예외가 하나라도 있으면 `run_pip_audit.py`가 exit 1로 실패한다(L106-112). 예외는 CI가 자동으로 적용하지 않는다 — 사람이 `.pip-audit-ignore`를 직접 수정한다.

### (c) 예외 자동 적용 금지

`.pip-audit-ignore`에 등록된 예외를 CI가 자동으로 적용하지 않는다. 예외는 사람이 검토한 뒤 직접 등록한다. 이는 게이트 무력화를 방지한다(run_pip_audit.py L82-83: "유효기간이 지난 예외를 자동으로 계속 무시하면 게이트가 무력화된다").

## 대안: Python 3.11+ 이전 유지

dnspython 2.9.0의 수정판을 Python 3.10에 적용하려면 `requires-python`을 3.11+으로 상향해야 한다. 그러나:

- 이 저장소는 3.10 런타임을 지원 중(실제 CI/로컬 모두 3.10.11 검증, commit 7dfb3f3ed).
- 3.11+ 이전 영향이 크다(전체 개발 환경·CI 파이프라인·운영 런타임 재검토).
- 별도 프로젝트/브랜치에서 3.11+ 이전을 준비하는 것이 적절하다.

따라서 3.10 지원 유지 중에는 dnspython에 대한 기한부 예외로 충분하다.

## 결과/위험/재평가 시점

- **현재 위험**: dnspython CVE-2026-114605(DoS)는 email-validator 전이 의존이며 앱이 DNS 조회를 하지 않아 도달 불가. 그러나 pip-audit 게이트가 예외 만료(2026-11-27) 시 CI 자동 실패.
- **재평가 시점**: 2026-11-27 이전. Python 3.11 이상 이전 또는 3.10용 패치 릴리스가 나오면 재평가.
- **추가 위험**: lock 파일 재생성 시 의존성 충돌이 발생하면 scratch venv 설치가 실패하여 수리 시간이 길어진다. 이 경우 major 범프로 분류하고 CTO에 에스컬레이션.

## 근거

- `scripts/run_pip_audit.py` L1-26: pip-audit 래퍼 동작 설명
- `scripts/run_pip_audit.py` L78-112: `.pip-audit-ignore` 파싱 및 만료 체크
- `scripts/run_pip_audit.py` L272-294: lock/local-only 취약점 분리 메시지
- `.pip-audit-ignore`: CVE-2026-114605 예외(사유·만료 2026-11-27)
- `requirements-lock.txt` L44: dnspython==2.8.0 핀
- commit 7dfb3f3ed: "Python 3.10 호환 보안 버전 고정" (filelock/setuptools)
- commit 0a895f1ec: "msgpack 1.2.2 -> 1.2.3 (CVE-2026-114613)"
- commit 2ea5b6b94: "dnspython CVE-2026-114605 만료 예외(2026-11-27)"
- ADR-2026-10-23-A 결정 1′: "보안 범프(pip-audit 적발)는 로컬 워커 자동 허용(패치·마이너)"
