# 감사: 적대 감사 6/6 — 빈틈 유형 6종 전수 점검

- **날짜:** 2026-10-01
- **워커:** backend-2
- **작업:** task-10665
- **범위:** `C:\aios\wt\backend-2`(=`C:\aios\aios` 코드베이스, 워크트리) 전체 + `C:\aios\pm`(함대 엔진, 읽기 전용)
- **성격:** 감사 보고서만 작성. 코드 수정 없음(spec 명시).
- **방법:** Grep/Glob/Read 기반 3개 조사 축(① 거짓신호·무음실패, ② 규칙공백·지표불일치, ③ 자금안전장치·굳은설정)
  병렬 조사 후 종합. `C:\aios\pm`은 전 과정 Read/Grep만 사용, 어떤 수정도 하지 않음.

## §0 P0 요약 (자금·데이터 손실·거짓 녹색)

| ID | 유형 | 위치 | 내용 | 근거 |
|----|------|------|------|------|
| P0-1 | (2) 무음실패→거짓녹색 | `scripts/closeout/healthcheck.py`의 `_write_closeout_ci_report()` (closeout ci_report 스키마) | ci_report/guard_report 스키마 불일치(`ok` vs `passed` 필드) — healthcheck가 쓰는 필드와 local_ci가 쓰는 필드가 달라, closeout이 실제 CI 적색을 통과로 오판할 수 있는 경로(task-6497 계열) | 조사 에이전트1 보고, `scripts/closeout/ops.py:53-71` 간접 확인 |
| P0-2 | (3) 규칙공백 | `C:\aios\pm\auto_decision.py` 3660-3662 else 분기 | healthcheck가 낼 수 있는 code 중 60개 이상이 auto_decision에 분기가 없어 "unknown code"로 누적 → ND-23 rule_gap 리프 반복 발행 → 사람 판정으로 귀결(task-7746, task-8512, task-8624에서 실제 관측) | 조사 에이전트2 보고, `auto_decision.py:3242-3662` 대조표 |
| P0-3 | (5) 자금안전장치 누락 | `src/services/execution_service.py:86-174` `create_execution()`/`convert_to_live()` | LIVE 포함 모든 실행(execution) 생성·전환이 `record_audit_log()`를 호출하지 않음 — 자본 배정(최대 수만 달러 규모)이 승인 흐름은 타지만 감사 로그에 남지 않음. FD-7.2 audit_log 요구와 불일치 | 조사 에이전트3 보고, `execution_service.py:86-174` |
| P0-후보 | (2) 무음실패 | `C:\aios\pm\auto_decision.py:95-96` | escalation 파일 읽기 실패 시 로그 없이 `[]` 반환 — owner 미배정 상태가 조용히 지속될 수 있음 | 조사 에이전트1 보고 |

P0-1·P0-2·P0-3는 각각 "거짓 녹색으로 적색을 놓침", "결함이 사람 판정으로 새서 자동화 가치가 식음", "자금 이동인데 감사 흔적이 없음"에 해당해 spec의 P0 기준(자금·데이터 손실·거짓 녹색)에 직접 해당한다고 판단. 담당 풀: ops(P0-1, P0-2), backend(P0-3).

---

## §1 유형(1) 측정이 틀려 거짓 신호

**점검 범위:** `scripts/check_*.py` 게이트, `tests/` 내 perf 마크 테스트, `perf-measurement-baseline.json`, closeout 체크리스트.
**방법:** perf 측정 가드 스크립트와 실제 perf 테스트의 타이머 방식(고정 baseline vs 자기보정) 대조, 최근 커밋(a100a82f9)이 수정한 4건과 미수정분 비교.

| ID | 위치 | 증상 | 근거/재현 | 심각도 | 권장 조치 |
|----|------|------|-----------|--------|-----------|
| G1-1 | `tests/unit/scripts/test_check_release_gate.py:403-411` | raw `time.perf_counter()` 측정 후 고정 절대값(5.0s) 단언 — 호스트 부하에 따라 거짓 적색/녹색 가능 | 해당 테스트는 xdist 동시 실행 시 불안정 재현 가능 | P1 | 상대 budget(자기보정) 또는 `process_time` 기반으로 전환 |
| G1-2 | `perf-measurement-baseline.json` | raw-timer perf 테스트 523건이 "grandfathered" 상태로 게이트 통과 처리됨. a100a82f9에서 4건만 자기보정으로 전환, 나머지 519건은 여전히 환경 부하에 민감 | `check_perf_measurement_guard.py` 실행 시 OK로만 보고되고 519건의 잠재 불안정성은 드러나지 않음 | P1 | 별도 task로 단계적 전환, 당분간 baseline 파일 상단에 "잔여 519건 미전환" 명시 |

**소견 수:** 2건 (P1×2).

---

## §2 유형(2) 실패했는데 원인이 안 보임

**점검 범위:** `src/`, `scripts/`, `C:\aios\pm` 전체의 무음 except/무음 fallback, 타임아웃 사유 미표시, closeout ci_report 스키마.
**방법:** `except ... : pass`/`return []`/`return False` 류 패턴 grep 후 호출 맥락 확인.

| ID | 위치 | 증상 | 근거/재현 | 심각도 | 권장 조치 |
|----|------|------|-----------|--------|-----------|
| G2-1 | `C:\aios\pm\auto_decision.py:95-96` | `except (OSError, ValueError): return []` — escalation 파일 로드 실패가 로그 없이 넘어감 | esc-*.json 손상/삭제 시 owner 배정 실패가 무음 | P1 | `logger.warning()` 추가, 또는 cycle 실패로 전파 |
| G2-2 | `C:\aios\pm\auto_decision.py:180-181` | `except (OSError, subprocess.TimeoutExpired): return []` — git 스캔 타임아웃이 일반 실패와 구분 없이 빈 리스트로 처리 | repo 스캔 30초 타임아웃 시 ND-2 실패 파일 추적이 공백으로 남음 | P1 | timeout과 error를 분리 로깅, ND-2e 마커와 일치시킴 |
| G2-3 | `C:\aios\pm\auto_decision.py:2843-2844` | `except (ValueError, TypeError): return False` — 타임스탬프 파싱 실패가 무음으로 `_is_task_recent()` 거짓 음성 유발 | 잘못된 날짜 형식의 task note 입력 시 | P2 | 실패 시 "매우 오래됨"으로 간주하거나 로그 |
| G2-4 | `C:\aios\pm\mcp\health.py:63-64` | `except (asyncio.TimeoutError, ProcessLookupError): pass` — MCP probe 타임아웃 후 프로세스 정리 실패가 무음 | 핸드셰이크 타임아웃 후 `proc.wait()` 재타임아웃 시 좀비 프로세스 가능성 | P2 | 명시적 로그, `proc.terminate()` 재시도 |
| G2-5 | `scripts/closeout/healthcheck.py` `_write_closeout_ci_report()` | closeout이 쓰는 ci_report 필드(`passed`)와 local_ci가 쓰는 필드(`ok`)가 달라 스키마 불일치 시 실제 CI 적색을 통과로 오판 가능 (= P0-1, §0 참조) | `scripts/closeout/ops.py:53-71`에서 양쪽 스키마를 다른 순서로 읽음 | **P0** | 필드명 통일, 또는 두 스키마를 명시적으로 모두 검증해 불일치 자체를 적색으로 처리 |

**소견 수:** 5건 (P0×1, P1×2, P2×2).

**미점검:** `C:\aios\pm`의 `test_healthcheck_*.py` 40여 개 개별 로깅 여부 전수 미검(표본만 확인), `local_ci.py`의 stdout/stderr/traceback 직렬화 경로 전수 추적 미완료.

---

## §3 유형(3) 규칙 공백으로 사람 항목이 생김

**점검 범위:** `C:\aios\pm\auto_decision.py`의 `process_escalations()` 분기 전수(라인 3242-3662) vs `C:\aios\pm\healthcheck.py`가 낼 수 있는 `esc["code"]` 전수.
**방법:** 두 목록을 대조.

| ID | code | 증상 | 근거 | 심각도 |
|----|------|------|------|--------|
| G3-1 | (60+ 미분류 code 전반) | healthcheck가 실제로 내는 code 중 60개 이상이 auto_decision에 대응 분기가 없어 매 폴링 "unknown code" → 반복 시 ND-23 rule_gap 리프 자동 발행 → 결국 사람 판정으로 귀결 | `auto_decision.py:3242-3662`(else 분기), `healthcheck.py` 전수의 `esc["code"]=` 할당 지점 | **P0**(= P0-2, §0) |
| G3-2 | `task_long_running` | 6회 관측 후 ND-23 rule_gap 리프(task-7746) 발행됐지만 이후에도 분기가 추가되지 않아 폴링마다 여전히 unknown code | `auto_decision.py:3429-3465` 주석, `healthcheck.py:1956` | P1 |
| G3-3 | `pg_crash_recovery` | requires_human 정책 없이 매 크래시마다 owner 리프가 무한 재생성(15~20분 간격) | `auto_decision.py:3500-3516` | P1 |
| G3-4 | `ledger_guard_rejects` | "자동 해소" closed 후에도 폴링마다 owner 미배정 10분 뒤 재부상, 190건 누적 | `auto_decision.py:3536-3547` | P1 |
| G3-5 | `local_candidate_supply`, `local_queue_depth`, `commit_charge_high` | 매칭 분기 없어 폴링마다 unknown code 반복 기록(task-6952, task-6954, task-7809) | `auto_decision.py:3414-3428`, `3492-3499` | P1 |
| G3-6 | `proc_down_*`, `proc_stale_*` | 동적 접미사 code라 `startswith` 매칭(3609-3621)으로만 부분 대응, 신규 프로세스명 패턴은 미보장 | `auto_decision.py:3609-3621`, `healthcheck.py:637,670` | P2 |

**소견 수:** 6건 (P0×1, P1×4, P2×1).

**미점검:** healthcheck 전용 60+ code(`backup_drill_failed`, `risk_replay_stale`, `ram_low`, `disk_low` 등) 각각에 대한 개별 분기 필요 여부 심사 — 목록 추출만 했고 건별 우선순위 매김은 다음 리프로 넘김.

---

## §4 유형(4) 지표가 목표와 다른 것을 잼

**점검 범위:** MVP-1 종결 지표 8개(`dashboard_mvp1.py` `mvp1_process()`, 라인 100-205) 각각의 사용자 가치 연결성.
**방법:** 각 지표가 사용자 체감(성능·기능·편리함·전략 성과)과 직접/간접/무관 중 어디에 해당하는지 판정.

| 지표 | 사용자 가치 연결 | 판정 근거 |
|------|-----------------|-----------|
| ① 명세 리프 구현 | 직접 | done/total — 구현 완료가 기능 존재와 직결 |
| ② 마일스톤 리프 | 간접 | 정정 리프 수 = 발견된 결함 수정 추이 |
| ③ 깊이 하한(D2/D3) 충족 | **무관에 가까움(P1)** | "테스트 깊이가 D2다"는 "기능이 정상 작동한다"와 동치가 아님 — 프로세스 증빙만 잼 |
| ④ 적대적 감사 6종(발견 0 두 번 연속) | **무관에 가까움(P1)** | "이번 라운드에 못 찾음"과 "결함이 없음"을 구분하지 못함. 감사를 느슨하게 할수록 지표가 좋아지는 역유인 존재 |
| ⑤ CI 연속 녹색 | 간접 | 게이트 위반 없음 = 최소 신뢰성 신호 |
| ⑥ 재오픈 0(7일) | 간접 | 재발 없음 = 근본 수정 가능성 |
| ⑦ 자기조치 비율 ≤10% | **무관에 가까움(P2)** | 자동 해소 비율은 환경 안정성 신호이지 기능 완성도와 무관 |
| ⑧ 종결 게이트(12항, J1~J3 E2E 포함) | 직접 | 실제 사용자 경로 E2E 검증 포함 |

| ID | 소견 | 근거 | 심각도 | 권장 조치 |
|----|------|------|--------|-----------|
| G4-1 | 지표③(깊이)이 프로세스 증빙만 재고, 실제 기능 동작 여부와 분리되어 있음 | `dashboard_mvp1.py:132-147`, CLAUDE.md §5 D2/D3 정의 | P1 | ⑧ 종결 게이트(E2E)와 연동: D?인 리프라도 E2E가 해당 경로를 덮으면 결함 없음으로 간주하는 보정 규칙 추가 |
| G4-2 | 지표④(감사 발견 0)가 "감사 활동 완료"와 "실결함 0"을 구분하지 못함 — 느슨한 감사가 지표를 올리는 역유인 | `dashboard_mvp1.py:149-168` | P1 | 발견 0 "횟수"가 아니라 "남은 Open 결함 수 + 심각도 가중"으로 대체 |
| G4-3 | 지표⑦(자기조치 비율)이 환경 안정성 신호이지 사용자 가치 신호가 아님에도 종결 게이트에 동일 비중으로 포함 | `dashboard_mvp1.py:184-191` | P2 | 보조 지표로 재분류하거나 가중치 하향 |

**소견 수:** 3건 (P1×2, P2×1).

**미점검:** `spec_progress`(명세별 done/total) 산출 로직의 세부 정확성, `depth_judge.py`의 D2/D3 자동 판정 알고리즘 — 둘 다 존재는 확인했으나 판정 로직 내부 결함 여부는 범위 밖(별도 리프 권장).

---

## §5 유형(5) 돈이 걸린 행동의 안전장치

**점검 범위:** 주문 생성/취소, 출금, 모드 전환(paper↔live), 거래소 키 등록/변경, 전역 정지/재개 — 각 API 라우터와 서비스 로직.
**방법:** 각 진입점에 대해 확인단계/권한/멱등(105 표준)/감사기록/되돌리기 유무 확인.

| 진입점 | 확인단계 | 권한 | 멱등(105) | 감사기록 | 되돌리기 | 심각도 |
|--------|---------|------|-----------|----------|----------|--------|
| 거래소 키 등록 (`exchange_credentials.py:45`) | 키 검증만 | ✓ `get_current_user` | ✗ | ✓ `record_audit_log` | ✓ revoke | P2 |
| 거래소 키 해지 (`exchange_credentials.py:66`) | 없음 | ✓ | ✗ | ✓ | 부분(물리 삭제 불가, 논리만) | P2 |
| Execution 생성(LIVE/PAPER) (`execution_service.py:86`) | LIVE는 승인 필요 | ✓ | ✗ | **✗ 없음** | ✓ retire 가능 | **P0** |
| Execution LIVE 전환 (`execution_service.py:132`) | 생성 재사용(승인) | ✓ | ✗ | **✗ 없음** | ✓ 추적됨 | **P0** |
| 주문 제출 (`submit_order.py`) | 사전 게이트 평가 | ✓ 소유권 확인 | ✓ (route,tenant,subject,digest) | ✓ | ✓ FAILED/UNKNOWN 상태 | - |
| Paper 배포 시작 (`paper_control.py:87`) | 리스크 게이트(EO-05) | ✓ | ✓ idempotency_key | 별도 확인 필요 | ✓ 상태기계 관리 | - |
| Paper 배포 일시정지/정지 (`paper_control.py:133-165`) | - | ✓ | 바디에 key는 받으나 핸들러 적용 여부 미확인 | - | - | P1 |
| 킬스위치 활성화 (`risk_gate.py:114`) | 없음 | ✓ user/admin | **✗** | ✓ `record_command_event` | ✓ deactivate | P1 |
| 킬스위치 비활성화 (`risk_gate.py:147`) | evidence_ref 필요하나 "이미 INACTIVE" 재검증 불명확 | ✓ | **✗** | ✓ | ✓ fail-closed | P1 |
| 킬스위치 복구(override) (`risk_gate.py:170`) | evidence+승인+cooldown | ✓ MFA admin + break-glass | ✗ | ✓ 결정 기록 | ✓ 재진입 차단 | - |
| 출금(withdrawal) | — | — | — | — | — | **미점검** |

| ID | 소견 | 근거 | 심각도 | 권장 조치 | 담당 풀 |
|----|------|------|--------|-----------|---------|
| G5-1 | Execution 생성/LIVE전환이 감사 로그를 남기지 않음 (= P0-3, §0) | `execution_service.py:86-174` | **P0** | `record_audit_log()` 호출 추가 | backend |
| G5-2 | 킬스위치 활성화/비활성화에 멱등키 없음 — 네트워크 재시도 시 중복 control row 가능 | `risk_gate.py:114-161` | P1 | `require_idempotency_key()` + `run_idempotent()` 적용 | backend |
| G5-3 | 킬스위치 비활성화가 "이미 INACTIVE" 상태 재검증 없이 evidence_ref만으로 통과 가능해 보임(코드상 명확한 거부 로직 미확인) | `risk_gate.py:147-161` | P1 | INACTIVE 상태에 대한 명시적 거부 추가/확인 | backend |
| G5-4 | Paper 배포 정지/일시정지가 idempotency_key를 핸들러에서 실제로 적용하는지 미확인 | `paper_control.py:133-165` | P1 | `pause_deployment()`/`stop_deployment()` 내부 key 적용 여부 확인 | backend |
| G5-5 | Execution 시작(LIVE 포함)에 세션 재인증(step-up) 요구 없음 — 세션 탈취 시 고액 LIVE 실행 가능 | `execution_control.py:27-108` | P2(탈취 전제, 낮은 발생확률) | 화이트리스트 패턴(`reauthenticate`)과 정합 여부 아키텍처 결정 필요 | backend(L4 결정 선행) |
| G5-6 | 거래소 키 해지 시 캐시 무효화 실패에 대한 명시적 롤백/로그 없음(지연된 재검증으로 완화되긴 함) | `exchange_credentials.py:66-75` | P2 | `resolver.invalidate()` try/except + 경고 로그 | backend |

**소견 수:** 6건 (P0×1, P1×3, P2×2).

**미점검:** 출금(withdrawal) API 라우터 자체를 `src/api/routers/`에서 찾지 못함 — PLT-20류 docstring상 별도 리프로 이연된 것으로 보이나 확정 못 함. Paper 배포 `retire` 핸들러의 롤백 의미론, 거래소 어댑터(Bitget/KIS) 내부 킬스위치 전파·fail-closed 주문 제출, 원장 동시 체결 포스팅 원자성, 감사 테이블 해시체인 읽기 시점 검증.

---

## §6 유형(6) 굳은 운영 설정

**점검 범위:** `C:\aios\pm\pools.yaml`, `fleet_flags` 참조, 저장소 내 상수 임계값(timeout/재시도/rate limit 등)의 근거 주석 신선도.
**방법:** `pools.yaml` 최종 수정 시각과 코드 내 상수 주석의 날짜 확인, flag grep으로 미사용 여부 확인.

| ID | 위치 | 증상 | 근거 | 심각도 |
|----|------|------|------|--------|
| G6-1 | `C:\aios\pm\pools.yaml` | 최종 수정 2026-09-30 16:50 UTC — 감사 시점(2026-10-01) 기준 24시간 이내로 최신. 굳은 설정 징후 없음 | 파일 타임스탬프 확인 | - (문제 없음) |
| G6-2 | `fleet_flags` 전수 | 코드에서 참조되는 flag는 모두 사용 중으로 확인됨, 미사용(orphan) flag 미발견 | grep 전수 조사 | - (문제 없음) |
| G6-3 | 상수 임계값 주석 | 2026-08~2026-09 초 날짜의 스펙/ADR 인용 주석 다수 발견되나, 이들은 재시도횟수·타임아웃 등 "실측 대비 낡은 운영값"이 아니라 정규 스펙 인용(normative citation)으로 판정 — 날짜 자체가 낡아도 ADR은 불변 근거이므로 문제 아님 | 표본 검토 | P2(관찰, 조치 불요) |

**소견 수:** 1건 관찰(P2, 실질적 조치 불요) — 명확한 "굳은 설정" 위반 사례는 이번 표본 조사에서 발견되지 않음.

**미점검:** 상수 임계값 전수(저장소 전체의 모든 timeout/재시도/limit 상수)는 표본 검토만 진행. `ADR-2026-09-09-C` 성능 예산표와 실측 수치의 정량 대조는 수행하지 못함 — 별도 리프 필요.

---

## §7 유형별 점검 범위·방법·소견 수 요약

| 유형 | 범위 | 방법 | 소견 수 (P0/P1/P2) |
|------|------|------|---------------------|
| (1) 거짓 신호 | perf 게이트, baseline 파일, closeout 체크 | 고정 baseline vs 자기보정 대조 | 2건 (0/2/0) |
| (2) 무음 실패 | src/scripts/pm 전역 except/timeout 패턴 | grep + 호출 맥락 확인 | 5건 (1/2/2) |
| (3) 규칙 공백 | auto_decision 분기 전수 vs healthcheck code 전수 | 대조표 작성 | 6건 (1/4/1) |
| (4) 지표 불일치 | MVP-1 종결 지표 8개 | 사용자 가치 연결성 판정 | 3건 (0/2/1) |
| (5) 자금 안전장치 | 주문/출금/모드전환/키등록/킬스위치 전 진입점 | 확인/권한/멱등/감사/되돌리기 표 | 6건 (1/3/2) |
| (6) 굳은 설정 | pools.yaml, fleet_flags, 상수 주석 | 신선도·미사용 여부 확인 | 1건 관찰 (0/0/1), 위반 미발견 |

**총 소견:** 23건 (P0 3건, P1 13건, P2 7건).

---

## §8 미점검 목록 (종합)

- `C:\aios\pm`의 `test_healthcheck_*.py` 40여 개 전수 로깅 검증 (표본만 확인)
- `local_ci.py` stdout/stderr/traceback 직렬화 경로 전수 추적
- healthcheck 전용 60+ code 개별 분기 필요성 건별 심사
- `spec_progress`/`depth_judge.py` 내부 판정 로직 정확성
- 출금(withdrawal) API 라우터 소재 확인
- Paper 배포 `retire` 핸들러 롤백 의미론
- 거래소 어댑터(Bitget/KIS) 내부 킬스위치 전파·fail-closed 주문 제출
- 원장 동시 체결 포스팅 원자성, 감사 테이블 해시체인 읽기 시점 검증
- 저장소 전체 상수 임계값(timeout/재시도/limit) vs `ADR-2026-09-09-C` 예산표 정량 대조

---

## §9 권장 후속 리프

1. (P0) `scripts/closeout/healthcheck.py` ci_report 스키마 통일 — ops 풀.
2. (P0) `C:\aios\pm\auto_decision.py`에 healthcheck 미분류 60+ code 중 실제 발생 빈도 높은 것부터 분기 추가, 또는 "알려진 무해 code 화이트리스트"로 unknown 경로의 소음을 줄임 — ops 풀(단, `C:\aios\pm` 직접 수정 금지 규칙에 따라 ops task로 요청).
3. (P0) `execution_service.py`의 `create_execution()`/`convert_to_live()`에 `record_audit_log()` 추가 — backend 풀.
4. (P1) 킬스위치 활성화/비활성화에 멱등키 적용 — backend 풀.
5. (P1) MVP-1 지표③④⑦의 "프로세스 증빙 vs 사용자 가치" 재가중 — PM/ops 결정 필요.
6. (P1) perf-measurement-baseline.json 잔여 519건 단계적 자기보정 전환 — backend 풀, 별도 task 분할.
7. (P1) `execution_control.retire(liquidation="IMMEDIATE_MARKET")`가 실제 청산 주문을 내도록 구현하거나, 미구현 상태를 API 응답/문서에 명시 — backend 풀(§10-5 참조).
8. (P1) `/v1/foundation/evidence/timeline` 읽기 경로에 해시체인 검증 결합 여부 결정(매 읽기 비용 vs 주기적 배치) — backend 풀 결정 필요(§10-4 참조).
9. (P2) `open_order_sweeper`의 `adapter_failed` 목록을 로그 전용에서 알림/재시도 큐로 승격 — backend 풀(§10-2 참조).

---

## §10 보충: task-10782 — 미점검 5항목(출금·킬스위치 전파·원장 동시성·해시체인 읽기 검증·retire 롤백)

- **날짜:** 2026-10-01
- **작업:** task-10782 (parent: task-10665 §8 미점검 목록 보충)
- **성격:** §8 미점검 목록 중 자금 관련 5항목만 보충 점검. 코드 수정 없음.

### §10 요약

5항목 중 신규 P0는 없음(기존 P0-3/G5-1은 task-10779·task-10780에서 이미 수정 커밋됨 — 아래 (a)(b)에서 재확인). 신규 P1 2건((d) 해시체인 읽기 미결합, (e) retire IMMEDIATE_MARKET 미구현), P2 1건((b) 스윕 실패 무알림). (c) 원장 동시 포스팅은 점검 결과 **문제 없음**(기존 메커니즘이 견고).

### (a) 출금/정산(payout·withdrawal) 쓰기 경로

**점검 방법:** `출금|withdraw|payout` 전수 grep → 실제 쓰기 경로를 `src/foundation/ledger/application/payouts.py`(LC-15a)와 라우터 `src/api/routers/foundation/ledger_admin.py`로 추림. 확인/권한/멱등/감사/되돌리기를 코드로 직접 대조.

| 항목 | 내용 |
|------|------|
| 소재 | `schedule_payouts()`(정산배치 생성, PENDING_PAYOUT→AVAILABLE) / `mark_payout_paid()`(오프플랫폼 송금 확정, AVAILABLE→PLATFORM:PAYOUT_CLEARING) — `src/foundation/ledger/application/payouts.py:64-166`. API는 `POST /admin/ledger/payouts/{batch_id}/paid`(`ledger_admin.py:59-80`)만 존재 |
| 확인단계 | `mark_payout_paid`는 `external_ref`(수동 송금 증빙 문자열)를 바디로 받되 그 값 자체의 진위는 검증하지 않음 — "실제 돈이 플랫폼 밖에서 이미 이동했다"는 사실은 admin의 수동 입력을 신뢰 |
| 권한 | `get_current_admin` + `require_break_glass("tenant_read")`(break-glass scope). 주석(`ledger_admin.py:22-24`)이 "정확한 4번째 scope(`payout_confirm`)가 없어 가장 가까운 기존 scope로 근사"라고 명시 — **권한 세분화 미흡**(tenant_read는 원래 읽기 권한인데 쓰기에 재사용) |
| 멱등(105) | 생성(`create_batch`)은 `(seller_user_id, period_end)` UNIQUE + `ON CONFLICT DO NOTHING`(진짜 멱등 — 재호출 시 기존 배치 반환). 확정(`mark_paid`)은 `conditional_update`로 `RELEASED→PAID` 1회만 전이 — 이미 PAID면 `ConcurrencyConflictError`를 던짐(캐시된 성공 응답을 재반환하지 않음). 105 표준의 "조건부 UPDATE"는 충족하지만, 네트워크 재시도로 응답을 놓친 admin이 재호출하면 실패로 보여 혼란 가능(실제로는 이미 성공) |
| 감사 | `post_entry`가 같은 트랜잭션에서 `AuditAppender`로 분개를 남김(`payouts.py:108-110,160-162`) — 자금 이동은 전부 감사됨 |
| 되돌리기 | 없음 — `PAID`는 터미널 상태, 되돌리는 API/경로 미발견. 오류 송금 시 역분개(reversal entry)를 수동으로 새로 포스팅해야 함(해당 경로도 코드상 확인 못함) |

**결론:** 실제 "거래소로의 출금"(예: Bitget/KIS 계좌에서 은행으로 인출) 자동화 경로는 `src/` 전수에서 **발견되지 않음** — 이 플랫폼의 "출금"은 마켓플레이스 판매자 정산(셀러 수익금)이 전부이고, 그조차 실제 송금은 플랫폼 밖(수동/은행)에서 이뤄진 뒤 `mark_payout_paid`로 사후 확정만 한다. 따라서 "자동화된 자금 유출 경로"라는 의미의 공격면은 이 경로에 없음. 다만 권한 scope 근사(`tenant_read`)와 되돌리기 부재는 기존 소견 패턴(G5류)과 같은 유형의 개선 여지.

**근거:** `src/foundation/ledger/application/payouts.py:1-166`, `src/api/routers/foundation/ledger_admin.py:48-80`, `src/foundation/ledger/adapters/postgres_payout_repository.py:149-167`.
**재현/반증 테스트 위치:** `tests/integration/foundation/ledger/`(payout 관련 테스트 파일) — 이번 조사에서 실행은 안 함, 소재만 확인.
**심각도:** P2(권한 scope 근사), 자금 유출 자동화 경로 자체는 **해당 없음(N/A)**.
**담당 풀:** backend(scope 세분화 시).

### (b) 킬스위치 발동의 거래소 어댑터 fail-closed 전파

**점검 방법:** `src/exchanges/{bitget,kis,nh}`에서 kill switch 직접 참조 여부 grep(무결과 확인) → 신규 주문 제출 경로(`evaluate_pre_submit.py`, `outbox_dispatcher.py`)와 기존 미체결 정리 경로(`open_order_sweeper.py`) 각각의 호출 체인 추적.

**결론:**
- **신규 주문 제출(미래 방향):** 거래소 어댑터(Bitget/KIS/NH) 자체는 kill switch를 전혀 모름 — fail-closed는 어댑터가 아니라 **제출 전 단일 관문**(`PreSubmitGate`)에서 구현됨. `evaluate_pre_submit.py:105-131`이 `active_controls`(활성 safety_control)를 보고 `RISK_KILL_SWITCH_ACTIVE_{scope}` 사유로 거부한다. `outbox_dispatcher.py:97-115`는 `pre_send_gate`가 `None`이면 생성자에서 즉시 `ValueError`(I-01, 기본값 없음) — 게이트를 건너뛰고 어댑터를 호출할 수 있는 코드 경로가 구조적으로 막혀 있음(`_gate_allows()` 호출 후에만 `resolve_adapter`→`call_submit`, `outbox_dispatcher.py:191-205`). **설계상 fail-closed 확인됨.**
- **이미 제출된 미체결 주문(과거 방향):** `KillSwitchService.activate()`의 fan-out 중 `sweep_open_orders()`(`open_order_sweeper.py`)가 거래소별 `adapter.cancel_order()`를 호출하지만, 개별 어댑터 실패는 `adapter_failed` 리스트에 담겨 **로그(`logger.exception`)로만** 남고 알림/재시도로 이어지지 않음(`open_order_sweeper.py:243-249`, `kill_switch_service.py:177-198`의 `except Exception: logger.exception`도 동일 패턴). 즉 "킬스위치 활성화" 자체는 성공 처리되지만, 거래소에 실제로 취소 요청이 도달했는지는 개별 주문 단위로 확인되지 않고 운영자가 로그를 봐야만 안다 — §2 유형(2) "무음 실패" 패턴과 동일 계열의 갭.

**근거:** `src/foundation/risk_gate/application/evaluate_pre_submit.py:105-131`, `src/services/oms/application/outbox_dispatcher.py:97-115,191-205`, `src/services/safety/open_order_sweeper.py:174-271`, `src/services/safety/kill_switch_service.py:132-198`.
**재현/반증 테스트 위치:** `tests/integration/risk/test_pre_submit_gate_concurrency.py`(신규 제출 차단 측), `tests/unit/services/test_open_order_sweeper.py`(스윕 실패 처리 측 — adapter_failed가 로그 외 후속 조치로 이어지는지 확인 가능한 지점).
**심각도:** 신규 제출 차단은 문제 없음(N/A). 기존 미체결 정리 실패 무알림은 P2(이미 §2 G2-4/G2-5와 같은 유형, 신규 소견 번호는 부여하지 않고 동일 패턴으로 묶음).
**담당 풀:** backend.

### (c) 원장 동시 체결 포스팅의 원자성

**점검 방법:** `post_entry.py`가 호출하는 `BalanceRepository.get_for_update`/`apply`(`postgres_balance_repository.py`)의 락 전략을 코드와 docstring으로 대조. FA-10(no-UPDATE 트리거)으로 물리 행 락이 깨진 배경과 대체 메커니즘을 확인.

**결론:** 동일 계정에 대한 동시 포스팅은 **원자적으로 직렬화됨** — `get_for_update()`가 관련 `account_code`를 정렬(오름차순)된 순서로 `pg_advisory_xact_lock(hashtextextended(account_code, 0))`로 잠근 뒤(트랜잭션 종료 시 자동 해제, 데드락 회피를 위해 항상 같은 순서로 잠금), `apply()`는 `DELETE ... WHERE last_entry_seq = $expected` → `INSERT ... RETURNING`을 한 SQL 문으로 묶어 낙관적 버전 체크까지 수행한다(`postgres_balance_repository.py:85-156`). FA-10이 `ledger_balance`에 no-UPDATE 트리거를 건 이유로 물리 행 락(`SELECT ... FOR UPDATE`)이 DELETE+INSERT 체인에서 깨지는 실제 장애(`UnknownAccountError` 오탐)가 있었고, 이를 advisory lock으로 교체해 고쳤다는 과정이 docstring(`postgres_balance_repository.py:18-43`)과 회귀 테스트로 남아 있음. 두 계좌 간 이체처럼 여러 계정을 동시에 잠그는 경우도 정렬 순서 덕에 교착 없이 직렬화된다.

**근거:** `src/foundation/ledger/adapters/postgres_balance_repository.py:1-156`(특히 18-43, 85-156).
**재현/반증 테스트 위치:** `tests/integration/foundation/ledger/test_queries.py`의 `test_get_balance_no_false_positive_drift_under_concurrent_commits`(advisory lock 도입의 회귀 재현 테스트로 추정 — 이번 조사에서 실행은 안 함, grep으로 소재만 확인).
**심각도:** 문제 없음(관찰, 조치 불요).
**담당 풀:** 해당 없음.

### (d) 감사 테이블 해시체인의 읽기 시점 검증

**점검 방법:** `src/foundation/evidence/application/verify_audit_chain.py`와 그 호출부를 추적, 일반 감사 로그 열람 API(`GET /v1/foundation/evidence/timeline`)와 체인 검증 API가 같은 요청 경로인지 확인.

**결론:** 일반 열람(`GET /v1/foundation/evidence/timeline`, `evidence.py:25-42`, `get_current_user`로 아무 사용자나 호출 가능)은 해시체인을 **검증하지 않는다** — `get_audit_timeline()`은 단순 커서 페이지네이션 조회만 한다. 체인 검증은 완전히 별도의 관리자 전용 엔드포인트(`POST /v1/foundation/evidence/chain:verify`, `evidence.py:45-57`, `get_current_admin` + 명시적 `tenant_id` 쿼리)로만 존재하고, 코드 전수에서 이 함수를 주기적으로 호출하는 스케줄러/크론은 발견되지 않음(`verify_audit_chain` 호출부는 이 라우터 1곳뿐). 즉 변조된 감사 로그를 일반 사용자가 열람해도 변조 사실이 그 응답에 드러나지 않으며, 관리자가 명시적으로 `chain:verify`를 호출하지 않는 한 변조는 발견되지 않는다(배치/주기 실행도 미배선). 모듈 docstring의 "AUD-003 operational tool ... API not yet wired"는 **낡은 설명**이다 — 실제로는 라우터에 배선돼 있으나(`evidence.py:18,56`), "읽기 경로와 결합되지 않은 수동 관리자 도구"라는 실질은 그대로다.

**근거:** `src/api/routers/foundation/evidence.py:1-58`, `src/foundation/evidence/application/verify_audit_chain.py:1-19`.
**재현/반증 테스트 위치:** `tests/foundation/integration/evidence/test_audit_event_lifecycle.py` — 체인 검증과 timeline 열람이 분리돼 있음을 보이는 테스트가 있는지는 미확인(소재만 확인, 실행 안 함).
**심각도:** P1 — WORM 감사 테이블(해시체인)의 가치는 "변조 시 반드시 발견됨"인데, 상시 결합된 검증 없이 수동 호출에만 의존하면 변조 창이 "다음 관리자 수동 실행까지" 벌어진다.
**담당 풀:** backend(주기적 배치 결합 또는 읽기 경로 결합 여부는 성능 트레이드오프 결정 필요 — PM/backend 결정).

### (e) Paper 배포 / Execution retire 롤백 의미론

**점검 방법:** 저장소 전수에서 `retire` grep → `src/services/execution_control.py`(실제 구현 위치, `paper_control`에는 `retire` 개념 자체가 없음을 먼저 확인) → `retire_liquidation` 컬럼의 쓰기/읽기 지점 전수 추적 → 관련 테스트(`test_retire_*`) 존재 여부 확인.

**결론:**
- "Paper 배포 retire"라는 명칭의 핸들러는 `src/foundation/paper_control/`에 **존재하지 않는다**(§8 미점검 항목의 표현이 부정확 — 실제로는 `execution_control.retire()`가 PAPER/LIVE 공통 실행 종료 경로다. paper_control에는 start/pause/stop만 있고 별도 retire 개념이 없음).
- `execution_control.retire()`(`execution_control.py:253-319`)는 `status IN ('RUNNING','PAUSED')` 조건부 UPDATE로 `RETIRED`(터미널 상태)로 전이하고, 같은 트랜잭션에서 `record_audit_log()`를 호출한다(task-10779, G5-1 수정 반영 확인됨 — §0 P0-3는 이 커밋으로 해소된 것으로 보임, 코드 재확인 결과 일치).
- **"되돌리기"는 없다** — `RETIRED`에서 복귀하는 API/상태전이가 코드상 발견되지 않음(`status_machine` 류 재확인은 범위 밖). 의도적 터미널 상태로 보임(§6 FD-16 docstring과 일치).
- **`liquidation="IMMEDIATE_MARKET"` 옵션이 실제로 청산 주문을 제출하지 않는다** — `retire_liquidation` 컬럼은 `UPDATE ... SET retire_liquidation = $2`로 DB에 기록될 뿐(`execution_control.py:278-285`), 이 값을 읽어서 실제 시장가 청산 주문을 내는 코드 경로가 `src/` 전수에서 **발견되지 않는다**(`retire_liquidation`을 쓰는 곳은 이 UPDATE 한 곳, 읽는 곳은 마이그레이션의 CHECK 제약뿐). `tests/unit/api/schemas/test_execution.py`의 `test_retire_request_accepts_explicit_liquidation`도 스키마 기본값/파싱만 검증하고 실제 청산 실행은 검증하지 않는다. 사용자가 "즉시 시장가 청산"을 선택해도 포지션은 그대로 남을 수 있다 — 사용자 기대와 실제 동작의 불일치.

**근거:** `src/services/execution_control.py:253-319`, `src/db/migrations/versions/f2a3b4c5d6e7_strategy_executions.py:37-38`(CHECK 제약, 유일한 다른 참조), `tests/unit/api/schemas/test_execution.py:85-96`.
**재현/반증 테스트 위치:** `tests/integration/test_execution_control.py::test_retire_running_execution`(현재 통과하지만 liquidation 실행 자체는 단언하지 않음 — 반증 재현은 "`liquidation=IMMEDIATE_MARKET`으로 retire 후 거래소에 청산 주문이 제출됐는지" 단언을 추가하면 실패로 드러날 것으로 예상, 이번 조사에서 실제 실행은 안 함).
**심각도:** P1 — 롤백(되돌리기) 부재는 설계 의도로 보여 문제 아님(N/A)이지만, IMMEDIATE_MARKET 미구현은 자금 손실로 이어질 수 있는 기대-동작 불일치.
**담당 풀:** backend.

**미점검(§10 범위 내에서도 확인 못한 것):** 거래소 어댑터(Bitget/KIS/NH) 각각의 `cancel_order` 실패 시 거래소 측 실제 상태(진짜로 미체결로 남았는지)를 reconcile 루프가 몇 주기 안에 바로잡는지는 코드 소재만 확인했고 수렴 시간 실측은 하지 않음. `ledger_payout_batch`의 "되돌리기"(역분개) 경로가 코드상 전무한지 아니면 범용 정정(correction) 경로(`src/foundation/ledger/application/refund.py`, `chargeback.py` 등)로 흡수되는지는 이번 조사에서 교차 확인하지 못함.
