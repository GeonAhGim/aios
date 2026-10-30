# PY313_PREP — 해시·서명·멱등키·DB 저장값 입력의 enum 문자열 추적

> 목적: `docs/design/PY313_PREP_enum_format.md`가 미확인으로 남긴 항목을 닫는다.
> Python 3.11+에서 `(str, Enum)`의 `f"{x}"`/`format(x)`는 값(`'FILLED'`)이 아니라
> `'Status.FILLED'`를 낸다. 이 문자열이 해시·서명·멱등키·DB 저장값에 닿으면 기존 증적
> 체인·`replay_verify`가 깨진다. 아래는 각 지점을 코드로 추적한 결과(추측 없음).

## 0. 버전별 동작 (3.10 → 3.11+)

| 표현식 | 3.10 | 3.11+ | 버전 의존 |
|---|---|---|---|
| `f"{E.M}"` / `format(E.M)` ((str, Enum)) | `'M값'` | `'E.M'` | **예** |
| `str(E.M)` | `'E.M'` | `'E.M'` | 아니오 |
| `E.M.value` | `'M값'` | `'M값'` | 아니오 |
| `json.dumps(E.M)` ((str, Enum), default=str 무관) | `'"M값"'` | `'"M값"'` | 아니오 (str 서브클래스는 default 미호출) |
| `json.dumps(PlainEnum.M, default=str)` | `'"E.M"'` | `'"E.M"'` | 아니오 |

## 1. 추적 표

| # | 지점 | 입력식 | enum 도달 | 근거 |
|---|---|---|---|---|
| 1 | `src/services/order_service/submit.py:89` payload_hash | `f"{claimed.order_id}:{outcome.reason}"` | **아니오** | `SendOutcome.reason: str` (`dispatch_outcome.py:64`). 생성 지점 전부 문자열 리터럴 / `exc.kind.value` / `type(exc).__name__` (`dispatch_outcome.py:86-117`). `order_id`는 UUID. (`GateOutcome`이라는 타입은 이 경로에 없고, 실제 타입은 `SendOutcome`) |
| 2 | `src/services/order_service/repository.py:294` payload_hash | `f"{order_id}:{reason_code}"` | **아니오** | `reason_code: str` 파라미터 (`repository.py:276`) |
| 3 | `src/services/oms/application/inbox_processor.py:84` | `f"{order_id}:{provider_event_id}"` | **아니오** | `provider_event_id: str` 시그니처 |
| 4 | `src/foundation/paper_control/application/apply_safety_control.py:87` detail (DB 저장) | `f"RISK_GATE_SAFETY_CONTROL:{safety_control_id}:{reason}"` | **아니오** | `reason: str`, `safety_control_id: UUID` (`:40-41`). 호출부 3곳 모두 `body.reason`/`view.reason`(스키마 str) 전달 (`risk_gate.py:142,242`, `kill_switch_service.py:161`). `SafetyScope`((str, Enum))는 `:63` 이후 텍스트에 안 들어가고 비교용으로만 쓰임 |
| 5 | `apply_safety_control.py:63` 멱등키 | `f"risk-pause:{safety_control_id}"` | **아니오** | UUID |
| 6 | `journal_rules.py:189,220,248` 멱등키 | `f"fill:{order_id}:{fill_seq}"` 등 | **아니오** | `order_id: UUID`, `fill_seq: int` (`journal_rules.py:160-161`) |
| 6b | `record_fill.py:186`, `open_order_sweeper.py:230` 멱등키 | `f"fill:{command.order_id}:{command.fill_seq}"`, `f"sweep:{control_id}:{order_id}"` | **아니오** | `order_id: UUID`, `fill_seq: int` (v1.py:72-73); `control_id: UUID`, `order_id: UUID` (sweeper:178,216). UUID/int 리터럴만 사용. |
| 7 | `src/core/eventstore/canonical_json.py:23` | `json.dumps(data, sort_keys=True, default=str)` | 조건부 | (str, Enum)은 값으로 직렬화(버전 무관). plain Enum만 `'E.M'`(버전 무관). 표 §0 |
| 8 | `src/core/eventstore/append.py:71,88`, `replay.py:76,130` | `canonical_json(payload)` / `"|".join(...)` | 조건부(payload 내용 의존) | join 입력은 `event_type: str`, `digest` 등 str. payload 안의 (str, Enum)은 값으로 직렬화 |
| 9 | `src/foundation/evidence/domain/rules.py:45` payload_hash | `json.dumps(payload, sort_keys=True, default=str)` | 조건부 | #7과 동일 (버전 무관) |
| 10 | `evidence/domain/rules.py:65-77` event_hash | `"|".join([... outcome.value, ... classification.value ...])` | **아니오** | 이미 `.value` 사용 |
| 11 | `src/foundation/ledger/domain/hash_chain.py:36-47,63` | `side.value`, `currency.value`, `event_type.value` | **아니오** | 이미 `.value` 사용 |
| 12 | `src/foundation/market_data/domain/lineage.py:71-83,94,118` | `JSONEncoder(sort_keys=True, default=str)` | 조건부 | #7과 동일 |
| 13 | `src/foundation/validation/domain/rules.py:65,81` | `json.dumps(..., default=str)` | 조건부 | #7과 동일. `str(initial_equity)`(Decimal) 사용 |
| 14 | `validation/application/run_check.py:119` | `json.loads(json.dumps(metrics, default=str))` (JSONB 저장) | 조건부 | #7과 동일 |
| 15 | `src/exchanges/bitget/private_ws_mixin.py:132` raw_hash | `json.dumps(row, sort_keys=True, default=str)` | **아니오** | `row`는 거래소 원본 JSON dict |
| 16 | `src/foundation/research_data/adapters/sources/rss_generic.py:252`, `sec_edgar.py:164` | `f"{feed_id}:{entry.guid}"`, `f"{accession}:{form}:{filing_date}"` | **아니오** | rss: `feed_id: str` (RssFeedRegistration:123), `entry.guid: str` (RssFeedEntry:129, XML text). sec_edgar: `accession: str`, `form: str`, `filing_date: str` (파서 파라미터 §151-159); JSON 배열 원소 모두 str. |
| 17 | `src/services/safety/liquidation_planning.py:136` hmac | `str(row["id"])` | **아니오** | DB row id (UUID) |

"조건부" = 코드상 payload에 (str, Enum)이 들어갈 수는 있으나 `json` 경로라서 3.10/3.11+ 결과가 같다.
plain `Enum`이 들어가면 `default=str`로 `'Class.MEMBER'`가 되지만 이것도 버전 무관이다.

## 2. 결론

- 3.11+에서 달라지는 **f-string/format 경로로 enum이 해시·멱등키·DB 저장 텍스트에 닿는 지점: 확인된 것 없음.**
  → **수정 필요 항목 없음** (src 변경 불필요).
- #6b, #16도 확정 완료: 모두 str/UUID/int 리터럴만 사용, enum 도달 경로 없음.

## 3. 고정 테스트

`tests/unit/py313_prep/test_enum_hash_inputs_pinned.py` — #1, #4/#5, #7~#13 및 negative
(plain Enum, `str(E.M)` 비교)를 리터럴 기대값으로 고정. #6b (record_fill, open_order_sweeper)
및 #16 (rss_generic, sec_edgar)도 고정 테스트로 커버. 3.10과 3.12에서 모두 통과 확인.
