# ADR-2026-10-15-A — 수렴 모드: CI 계층화 · 골든패스 판정 · 좁은 실패 카드

- 상태: Accepted (2026-10-15, CTO 결정, 사용자 제안 반영)
- 관련: ADR-2026-09-26-C(안정화 종결 지표), ADR-2026-09-26-D(통합테스트 DB 격리), ADR-2026-09-30-B(머지 큐)

## 배경 (실측)

- 구현은 사실상 끝났고 남은 일은 수렴이다. 24h 발행의 77%가 자기조치 리프(고아 회수, `health:ci_red`)다.
- full CI는 40분짜리 단일 신호다. 2026-10-15 하루에 같은 근본 원인(자식 pytest가 xdist 워커 DB를 삭제,
  이어서 orders 잔재 sentinel의 워커 전체 연쇄)을 잡는 데 full 3회(약 2시간)를 썼다.
- 15k 테스트가 워커당 하나의 가변 DB를 공유하므로 **한 테스트의 잔재가 그 워커의 나머지 전부를 적색으로 만든다**
  (287건 → 87건, 실제 결함은 1~2개). 신호가 뭉개져 좁은 수정 리프로 쪼갤 수 없다.
- 로컬 워커는 "전체 맥락 이해"가 필요한 리프에서 컨텍스트/턴 한도로 실패한다. 좁고 자기완결적인 리프는 100% 머지된다
  (claude-local 24h 머지율 40/40).

## 결정

1. **모드 구분.** 함대는 두 모드를 가진다. `implement`(스펙→리프→기능, 넓은 맥락)와 `converge`(신호→근본 원인 1건→좁은 수정).
   MVP-1은 `converge`다. converge에서는 (a) 새 스펙·게이트 정리(PERF/RATCHET/LANG) 발행을 멈추고, (b) 적색 1개는 근본 원인
   서명 1개에 owner 1명(systemic 리프)을 두며 중복 정정 리프는 스폰 전 재검사로 닫고, (c) 성공 지표는 리프 수가 아니라
   **녹색까지 걸린 시간·재오픈 0·flake 비율**이다. (ADR-2026-09-26-C 지표와 같다.)
2. **CI 3계층.**
   - T0 커밋(분 단위): ruff/mypy/guards + 변경 영향 테스트(현행 commit 모드).
   - T1 도메인 스위트(머지 큐 배치, ~10분): full pytest를 **논리 도메인**(OMS·원장·시장데이터·백테스트·API·보안/RLS·플랫폼)으로
     나눠 도메인마다 **자기 템플릿 클론 DB와 자기 sentinel**을 쓴다. 적색은 그 도메인에서 끝나고 재실행도 그 도메인만 한다.
   - T2 야간 전체(현행 full): 전 도메인 + perf + replay_verify + 커버리지. 신호가 아니라 **확인**이다.
3. **골든패스를 릴리스 판정 기준으로 승격한다.** 이미 있는 것을 쓴다: 백엔드 `tests/e2e/test_e2e_1..5`(페이퍼 주문 수명주기,
   watchdog 청산 …)와 프런트 `journey-j1/j2/j3/j9`. 이를 **T1과 별도 `golden` 게이트**(작은 고정 집합, 격리 DB, ≤ 5분)로 묶어
   "골든패스 녹색 = 출시 가능, 나머지는 회귀 신호"로 읽는다. 골든패스 적색은 P0, 도메인 스위트 적색은 도메인 owner로 라우팅한다.
4. **좁은 실패 카드(failure card).** CI 적색 1건마다 엔진이 자동 생성한다: 실패 nodeid · 첫 오류 3줄 · 대상 파일 ≤ 2개 ·
   해당 도메인 계약 문서 링크 · 재현 명령 · 완료 증거(그 nodeid 단독 통과 + 도메인 sentinel 통과). 카드는 로컬 레인 크기 한도
   (max_context_tokens 39k) 안에 들어가도록 만들고, 안 들어가면 `local_logical_split`의 AST 범위 분할로 더 쪼갠다.
5. **도메인 계약 문서.** `docs/contracts/<domain>.md`에 도메인별 불변식(예: 주문은 이벤트 없이 상태가 바뀌지 않는다, 원장은
   append-only)과 통과 판정 조건(종료 코드·sentinel·replay 기준)을 한 곳에 박는다. 로컬 워커는 카드 + 계약 문서만 읽고 일한다.
   게이트를 완화하는 변경은 계약 문서에서만 사람이 승인한다.

## 비목표 / 가드

- 게이트·sentinel을 느슨하게 하지 않는다(DECISION_GUIDELINES B-2). 쪼개는 것이지 약하게 하는 것이 아니다.
- 기존 full CI는 T2로 남겨 두고, T1·golden이 한 번 안정된 뒤에 T2 빈도를 낮춘다.

## 실행 순서

1. `golden` 게이트 정의(기존 e2e/journey 집합 고정, 격리 DB) — ops.
2. 실패 카드 생성기 + 도메인 라우팅 — ops(엔진).
3. 도메인 분할 맵(pytest 경로 → 도메인)과 도메인별 DB·sentinel — ops/backend.
4. 도메인 계약 문서 초안 — qa/backend(좁은 단위로 로컬 레인에도 배정).

## 개정 1 (2026-10-15, 실측 반영) — "구조를 새로 짓기 전에 신호부터 정리한다"

full CI 보고서 154건(2026-09-10~10-15) 실측: 녹색 5건(3%), 평균 40분(pytest 21분·pytest_perf 11분·frontend 8분),
pytest 적색 136건, 같이 적색인 단계는 coverage 87·pytest_perf 62·replay_verify 50이다. 적색 조합 상위가
`pytest`, `coverage+pytest+replay_verify`, `coverage+pytest+pytest_perf`다 — **coverage·replay_verify는 pytest의
파생**이라 독립 신호가 아닌데 단계마다 에스컬레이션과 정정 리프를 따로 만든다(오늘 같은 원인으로 esc 4개). 또 backend 단계
평균은 e2e 0.5분·journeys 0.6분으로 골든패스는 이미 싸다. 커밋 단위 영향 테스트 선택(ci_impact)과 머지 큐도 이미 있다.

따라서 결정 2의 T0/T1은 **대부분 이미 존재**한다. 새 도메인 DB 구조를 짓는 것(비싸고 위험)보다 아래를 먼저 한다.
순서는 효과/비용 순이다.

1. **파생 단계 접기.** pytest 적색이면 coverage·replay_verify·pytest_perf 적색은 `blocked_by: pytest`로 표시하고 별도
   에스컬레이션·정정 리프를 만들지 않는다(루트 서명 1개). 게이트 자체는 그대로 적색이다 — 라우팅만 접는다.
2. **nodeid 실패 원장.** `ci/failure_ledger.jsonl`: nodeid별 최초 발견 sha·마지막 발견·적색 횟수/실행 횟수·유형(결정적/간헐/연쇄).
   연쇄(같은 워커의 sentinel 다수)는 **최초 보고 지점 1건**(오류 요약은 발생 순서이므로 첫 항목이 오염 원인)으로 접고,
   간헐은 격리·건너뛰기가 아니라 **정정 대상**으로 올린다(게이트 완화 금지). 실패 카드는 이 원장에서 만든다.
3. **golden 게이트를 머지 큐 배치 게이트에 포함한다.** e2e+journeys 합 ~1분이므로 모든 배치에 붙여도 부담이 없고, 야간 full까지
   기다리지 않고 "출시 가능"을 매 배치 판정한다.
4. **full은 on-demand 우선.** 정정 배치가 착지하면 owners_fixed 요청으로 1회(시간당 1회 상한), 20커밋 트리거는 낮춘다.
   3일 연속 녹색 지표는 야간 full로만 센다.
5. **도메인 DB 분할은 보류.** 2번의 원장이 "오염원은 소수 테스트 파일"임을 확인해 주면 그 파일을 ADR-2026-09-26-D(모듈별 템플릿
   클론)로 옮기는 것으로 충분하다. 원장이 넓은 오염을 보일 때만 도메인 분할로 간다.
6. **로컬 워커용 도메인 계약 문서는 유지**하되, 카드(2번)가 안정된 뒤 실패가 반복되는 도메인부터 쓴다.

## 개정 2 (2026-10-16) — 자동 발행기 상한

실패 카드 생성기가 대량 적색 때 nodeid마다 리프를 발행해 1394건이 쌓인 사고의 재발 방지. 규칙: (1) 단계당 카드 상한 12(`MAX_CARDS_PER_STEP`),
초과분은 오류 서명으로 접고 나머지는 overflow 1카드, (2) 연쇄 판정은 `Sentinel:` 접두어, (3) 카드 리프 role은 backend(로컬 자동 배정 대상),
(4) 폭주를 닫을 때는 파생 QA 번들까지 함께 닫는다.

## 개정 3 (2026-10-21) — 학습 기록과 테스트 작성 규칙

사고 레지스트리·진단 레시피는 fleet 저장소 `docs/OPS_CONVERGENCE_PLAYBOOK.md`와 `C:\AiWorkKnowledge`(02 사고사례집 B-시리즈, 05 §7, 07 §9, 13)에 있다. 이 저장소 테스트에 적용되는 규칙만 여기 못 박는다.

1. **자식 `pytest`/`subprocess`를 띄우는 테스트**는 부모 xdist 워커의 DB를 건드리면 안 된다. `tests/support/db.py::effective_worker_id`(표식 `AIOS_TEST_WORKER_OWNER_PID="<pid>:<worker>"`)가 같은 worker id의 다른 pid에 고유 id를 준다. 진짜 워커는 `gwN`을 유지해야 한다 — `replay_verify`가 `{템플릿}_gwN` DB를 이름으로 다시 읽는다.
2. **orders를 직접 INSERT하는 테스트**는 종료(성공·실패 모두) 시 자기 행을 지우거나 일회용 DB 클론(ADR-2026-09-26-D)에서 돈다. 참고 패턴: `tests/integration/oms/test_gate_perf_residue_cleanup.py`. 이벤트 없는 `FILLED/SUBMITTED` 주문이 보이면 테스트 잔재가 아니라 제품 경로(`submit.update_from_exchange`가 `order_events`/`fills`를 안 씀)를 먼저 의심한다.
3. 워커 DB 생성/삭제 주체 추적: 환경변수 `AIOS_TEST_DB_AUDIT_LOG`(fleet `gate_env`가 `logs/pytest_full/db_audit.jsonl`로 설정).
4. 정확 일치 baseline 단언 금지.
