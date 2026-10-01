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
