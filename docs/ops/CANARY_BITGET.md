# Bitget 실계좌 소액 카나리아 (L4-30b, task-2750) 운영 안내

`scripts/canary_bitget.py`는 Bitget **실계좌**로 소액 왕복(place/get/cancel,
5 USDT 시장가 매수/매도)을 검증하는 1회성 운영 스크립트다. L4-30
(`tests/integration/exchanges/bitget/test_live_demo_roundtrip.py`)이 데모
계정으로 같은 종류의 왕복을 이미 검증했다 — 이 스크립트는 실계좌라는 이유로
그보다 훨씬 엄격한 하드가드 아래에서만 실행된다.

## 1. 지금 이 저장소의 실제 상태

이 커밋 시점(task-2750) 기준으로 다음 두 조건이 **둘 다 미충족**이다.

1. `docs/milestones/MVP-1_CLOSEOUT.md`가 존재하지 않는다.
2. task-2750의 `decision` 필드에 사용자 실행 승인이 기록돼 있지 않다.

따라서 `scripts/canary_bitget.py`를 실계좌 키로 호출하면
`assert_hard_guard_released()`가 즉시 `CanaryHardGuardBlockedError`를 던져
차단한다(`tests/unit/scripts/test_canary_bitget.py::
test_repo_hard_guard_is_currently_not_released`가 이 사실을 고정한다). 이
문서와 스크립트는 위 두 조건이 충족된 뒤 사람이 실행하기 위한 준비물이다 —
이 커밋에서 실계좌 주문을 실제로 낸 적은 없다.

## 2. 실행 전 체크리스트

1. `docs/milestones/MVP-1_CLOSEOUT.md`가 존재하는지 확인(ADR-2026-08-29-E
   Amended 2026-09-09).
2. task-2750 `decision` 필드에 사용자의 명시적 실행 승인이 기록돼 있는지
   확인 — 워커는 이 기록 없이 스스로 실계좌 키로 이 스크립트를 호출하지
   않는다.
3. `config/risk_policy/canary.yaml`의 상한(주문당 10 USDT, 세션 누적 50
   USDT, 심볼 `BTCUSDT` 1개, 최대 체결 2회)이 의도한 값인지 재확인.
4. `.env`에 아래 세 값을 채운다(주의: `BITGET_API_KEY`류가 아니다 —
   `tests/conftest.py`가 그 이름들을 항상 고정 테스트값으로 덮어쓰므로,
   실계좌 키는 별도 이름으로만 읽는다).
   ```
   BITGET_CANARY_API_KEY=...
   BITGET_CANARY_API_SECRET=...
   BITGET_CANARY_API_PASSPHRASE=...
   ```
5. kill switch가 ACTIVE가 아닌지 확인 — ACTIVE면 스크립트가
   `KillSwitchActiveError`로 즉시 중단한다(`assert_kill_switch_inactive`).

## 3. 실행

```
python scripts/canary_bitget.py --tenant-id <UUID>
```

성공하면 `docs/ops/CANARY_<실행일>.md`에 리포트가 생성된다(주문 id·체결
상태·3-way 대사 결과 포함). 리포트 생성 로직 자체는
`tests/unit/scripts/test_canary_bitget.py`의 `render_report`/`write_report`
테스트로 이미 검증돼 있다 — 실행 시점에는 실제 주문 결과만 채워 넣는다.

## 4. 안전 설계 요약

- 상한은 코드 상수가 아니라 `config/risk_policy/canary.yaml` 번들로
  관리한다(8.2-B 원칙, `personal-conservative.yaml`과 동일).
- `evaluate_pretrade_gate()`가 어댑터 호출 **이전에** 로컬에서 평가한다 —
  최소 수량 미만·주문당 상한 초과·화이트리스트 밖 심볼·누적 상한 초과·
  세션 체결 횟수 초과는 거래소로 전송되지 않고 REJECT된다
  (`submit_with_gate()`가 REJECT면 `adapter.place_order`를 호출하지
  않음 — mock 어댑터 테스트로 증명됨).
- `src/core/executor/executor.py`(FROZEN_PAPER_ONLY)를 거치지 않는다 —
  그 경로는 `mode != 'PAPER'`를 무조건 차단하므로(ADR-2026-08-29-E), 실계좌
  카나리아는 애초에 그 경로를 타지 않고 `BitgetAdapter`를 직접 호출한다.
- 키 값·서명은 어떤 로그·리포트·예외 메시지에도 보간되지 않는다
  (redaction 원칙).
