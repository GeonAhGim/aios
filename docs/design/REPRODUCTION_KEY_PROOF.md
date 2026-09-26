# ReproductionKey 해시체인: 실행 결과 증명 (task-7619, SRC-2)

## 0. 근거와 범위

ADR-2026-09-26-B Decision 2 (SRC-2): "실행 결과 증명은 `ReproductionKey` 해시체인으로
하며, ZK 실행은 매매 경로에 채택하지 않는다(사후 감사 증명 후보로만 기록)."

이 문서는 그 결정을 구체화한다: (a) 해시체인의 구성요소와 계산 순서, (b) 체인만으로 — 원문
스크립트/원시 데이터/재실행 없이 — 재현성을 감사하는 절차, (c) ZK 미채택 결정의 근거.

**본 문서는 설계 문서이며 신규 코드를 도입하지 않는다.** 인용하는 모든 함수/필드명은
`git grep`으로 실재를 확인했다 — 없는 것은 인용하지 않으며, 아직 배선되지 않은 부분은
아래에서 "미구현 — 설계만"으로 명시한다.

## 1. 이미 존재하는 구현 (BT-9, `src/foundation/backtest/domain/reproducibility.py`)

BT-9는 순수 조립 함수다 — 각 구성요소 값 자체의 계산은 상위/이웃 리프의 책임이고, BT-9는
그 값들을 하나의 키로 묶는 마지막 단계만 담당한다(모듈 docstring). 현재 구현된 4개 입력과
그 계산 위치:

| 구성요소 | 계산 함수 | 리프 |
|---|---|---|
| `script_hash` | `src/core/script/artifact/hash.py::script_hash()` | DSL-12 |
| `data_lineage_hash` | `src/foundation/market_data/domain/lineage.py::batch_hash()` | LA-23b |
| `rollup_version` | `src/foundation/market_data/domain/aggregation/timeframe_rollup.py::RollupResult.rollup_version` | DC-10 |
| `config_hash` | `src/foundation/backtest/domain/reproducibility.py::config_hash()` (내부에서 `BacktestConfigV2.canonical_json()` 사용) | BT-1 |

최종 조립은 `reproducibility.py`의 두 함수가 담당한다:

- `reproducibility_key_payload(*, script_hash, data_lineage_hash, rollup_version, config) -> dict[str, str]`
  — 네 입력을 검증(`_require_nonempty_str`)하고 `{"schema": HASH_SCHEMA, "script_hash": ...,
  "data_lineage_hash": ..., "rollup_version": ..., "config_hash": config_hash(config)}` 딕셔너리를
  만든다. `HASH_SCHEMA = "backtest-reproducibility-key-1"`.
- `reproducibility_key(*, script_hash, data_lineage_hash, rollup_version, config) -> str`
  — 위 payload를 canonical JSON(`sort_keys=True, separators=(",", ":"), ensure_ascii=True,
  allow_nan=False`)으로 직렬화한 뒤 `hashlib.sha256(...).hexdigest()`로 64자 16진 다이제스트를
  낸다. `HASH_ALGORITHM = "sha256"`.

Fail-closed: 네 입력 중 하나라도 비어 있거나 타입이 틀리면 `ValueError`로 거부한다(모듈
docstring: "do not disguise 'reproduced' with an incomplete key").

이 함수는 다른 리프에서 그대로 재사용된다 — 새 해시 알고리즘을 만들지 않는 것이 원칙이다:
`src/foundation/backtest/vector/experiment_ledger.py::record_grid_entry()`(BT-16b)는 그리드
스윕의 `seed`를 `script_hash` 문자열에 `f"{script_hash}:seed={seed}"`로 접어 넣은 뒤
`reproducibility_key()`를 변경 없이 위임 호출한다(그 모듈 docstring: "정의하는 새 해시 함수는
거부 사유" — BT-9가 해싱의 100%를 수행).

## 2. 체인의 하위 링크 — 각 구성요소도 "해시의 해시"다

`reproducibility_key`가 원자값이 아니라 **체인**인 이유는, 최상위 조립 이전의 각 구성요소
자체가 더 세부적인 원본 재료(스크립트 소스/IR 바이트, 원시 데이터 레코드, 설정 필드값)를
동일한 정규화 규칙(canonical JSON + sha256)으로 이미 한 번 해시한 결과이기 때문이다.

1. **`script_hash`** (`src/core/script/artifact/hash.py::hash_payload()` / `script_hash()`,
   `HASH_SCHEMA = "script-hash-1"`) — `{"schema", "grammar_version", "ir_version", "ir":
   to_bytes(ir).decode("utf-8"), "registry_version", "source"}`를 canonical JSON化 후 sha256.
   원문 소스는 정규화하지 않는다(공백 차이도 다른 아티팩트로 취급 — 모듈 docstring).
2. **`data_lineage_hash`** (`src/foundation/market_data/domain/lineage.py::batch_hash()`) —
   각 레코드를 정렬된 키의 canonical JSON 문자열로 만들고, 그 문자열들을 사전식으로 정렬한 뒤
   (입력 순서 무관), `\n` 구분자로 sha256 스트리밍 갱신한다.
3. **`rollup_version`** (`timeframe_rollup.py`) — 특정 실행의 데이터가 아니라 **집계 규칙
   자체**를 고정하는 버전 태그다: `ROLLUP_VERSION = f"tfr1-{hashlib.sha256(_ROLLUP_RULE_SPEC
   .encode('utf-8')).hexdigest()[:16]}"`. "이 봉의 데이터"는 `data_lineage_hash`가, "이 집계
   규칙"은 `rollup_version`이 각각 담당 — 두 역할은 분리되어 있다.
4. **`config_hash`** — `BacktestConfigV2.canonical_json()`(`models_v2.py`: `model_dump(mode=
   "json")`를 `sort_keys=True, separators=(",", ":"), ensure_ascii=True`로 직렬화)의 sha256.
   `slippage`/`commission`/`latency_ms`/`partial_fill`/`order_types`/`magnifier_tf`/`costs`/
   `adjustments`/`calendar` 전 필드가 입력이다.

각 링크가 sha256이므로 체인 전체가 단방향이다: 한 링크의 원본 1비트만 달라져도(애벌런치 효과)
그 링크의 다이제스트가 전부 바뀌고, 최종 `reproducibility_key`도 전부 바뀐다 — 반대로
`reproducibility_key`나 개별 링크 값만 보고 원본 소스 텍스트, 원시 데이터, 설정 필드값을
복원하는 것은 불가능하다.

## 3. 저장 스키마 — `ReproductionKey` (MP-1, `src/foundation/marketplace/contracts/v1.py:166`)

BT-9가 계산 로직이라면, `ReproductionKey`는 그 결과를 **검증자에게 전달하기 위한 wire/저장
스키마**다(frozen pydantic model):

```
script_hash, data_lineage_hash, rollup_version, config_hash, model_hash, key, schema_version
```

`script_hash`/`data_lineage_hash`/`config_hash`/`model_hash`/`key`는 64자 소문자 16진수
sha256 다이제스트로 shape 검증만 한다(`_validate_sha256_hex`) — **재계산하지 않는다**
(모듈 docstring: "Shape check only... never recomputes a digest"). `key`를 다른 필드들로부터
조립하는 것은 "BT-1의 일"이라고 이 계약 자체가 명시한다("Composing `key` from the other fields
is BT-1's job (`src/foundation/backtest`); this contract only validates that all five components
and the digest are well-formed hex strings.").

다이제스트뿐 아니라 구성요소 전부를 함께 들고 다니는 이유는 이 계약의 docstring에 그대로
쓰여 있다: "so a listing can be re-verified against a later backtest run (MP-11's
`verify_listing_backtest.py`) without needing to re-derive lineage out of a single hash." —
즉 §4의 감사 절차가 성립하는 근거가 바로 이 필드 구성이다.

### 3.1 미구현 — 설계만: `model_hash` 5번째 링크

`ReproductionKey`의 docstring과 스펙(`docs/specs/L4_analytics_authoring_backtest_marketplace_
v1.0.md` §3.4)은 5개 구성요소 공식을 명시한다: `sha256(script_hash ‖ data_lineage_hash ‖
rollup_version ‖ config_hash ‖ model_hash)` (스펙 원문: `model_hash` 기본값은 `sha256("")` —
AI-11 재현 동일성 DoD를 위함).

그러나 §1에서 확인했듯 BT-9의 실제 `reproducibility_key()`/`reproducibility_key_payload()`
시그니처는 `script_hash, data_lineage_hash, rollup_version, config` 네 개만 받는다 —
`model_hash` 파라미터가 없다. `ReproductionKey` 계약의 5필드 shape과 BT-9 구현의 4-입력 조립
사이에는 실제 코드 간극이 있다: **`model_hash`를 체인에 실제로 접합하는 조립 로직은 아직
존재하지 않는다.** 이 문서가 이를 "설계"로 남기는 이유는 이 간극을 감추지 않기 위함이다 —
AI-11(모델 기반 전략의 재현성)이 착수될 때 BT-9의 조립 함수가 5-입력으로 확장되거나,
`model_hash`가 없는 비-ML 전략에서는 스펙이 정한 기본값 `sha256("")`을 `reproducibility_key_
payload()` 호출 이전에 채워 넣는 별도 어댑터가 필요하다 — 어느 쪽이든 이 task의 범위 밖이며,
지금 코드베이스에 존재하지 않는 함수를 존재하는 것처럼 인용하지 않는다.

## 4. 감사 절차 — 재실행 없이 체인만으로 검증

검증자가 손에 쥔 것은 `ReproductionKey` 레코드 하나(예: 마켓플레이스 리스팅에 첨부되거나,
`ExperimentLedgerEntry.reproducibility_key`로 원장에 기록된 값)뿐이라고 가정한다. 아래 두
티어는 원본 스크립트/데이터/설정을 재실행하지 않는다 — sha256 재계산과 등치 비교만 한다.

**Tier 1 — 체인 자기정합성 검사 (레코드만 필요, 원본 재료 불필요).**
레코드의 `script_hash`, `data_lineage_hash`, `rollup_version`, `config_hash` 네 값을 그대로
가져와 `reproducibility_key_payload()`가 만드는 것과 동일한 딕셔너리
(`{"schema": "backtest-reproducibility-key-1", "script_hash": ..., "data_lineage_hash": ...,
"rollup_version": ..., "config_hash": ...}`)를 조립하고, 동일한 canonical JSON 규칙(`sort_keys=
True, separators=(",", ":"), ensure_ascii=True, allow_nan=False`)으로 직렬화한 뒤 sha256을
계산한다. 그 결과가 레코드의 `key`와 다르면 — 백테스트를 한 번도 돌리지 않고도 — 레코드가
위조되었거나 손상되었음이 즉시 드러난다.

**Tier 2 — 하위 링크 재계산 (원본 재료 필요, 재실행은 여전히 불필요).**
원본 스크립트 소스+IR, 원시 데이터 배치, 실제 `BacktestConfigV2` 객체를 손에 넣을 수 있다면,
각각 `script_hash()`, `batch_hash()`, `config_hash()`를 독립적으로 재계산해 레코드의 대응
필드와 비교한다. 세 함수 모두 순수 함수(no I/O, 시뮬레이션 없음)이므로 이 단계도 전략을
"실행"하지 않는다 — 저장된 재료를 해시하기만 한다.

Tier 1·2 모두 통과하면, "이 재료들로 계산했다고 주장하는 값이 실제로 그 재료들의 해시와
정합한다"는 것이 증명된다 — 이것이 "체인만으로 재현성을 감사한다"는 것의 정확한 의미다.
검증자는 원본 소스 텍스트나 데이터 내용을 다시 들여다볼 필요 없이(Tier 1) 혹은 재료를 손에
쥐고도 백테스트를 재실행할 필요 없이(Tier 2) 위조를 탐지한다.

**Tier 3 — 결과 재현 검증 (참고용, 이 체인의 범위 밖 — 재실행이 필요하다).**
`src/foundation/marketplace/application/verify_listing_backtest.py`(MP-11)의
`compute_result_hash()`(`RESULT_HASH_SCHEMA = "marketplace-listing-result-hash-1"`)는
"주장된 결과 해시"와 "실제로 `run_backtest`를 재실행해 얻은 결과의 해시"를 바이트 단위로
비교해 불일치 시 `MP_UNVERIFIED_RESULT`로 표시한다. 이 모듈의 docstring이 명시하듯 "config가
같다"는 사실은 이미 BT-9의 `reproducibility_key`로 별도 보증되어 있고, MP-11은 그 위에 "결과
숫자(fills/equity_curve/metrics)까지 같은가"를 확인하는 **더 강하지만 재실행이 필요한** 상위
레이어다. `ReproductionKey` 체인 자체(Tier 1·2)와 혼동하지 않는다 — 체인은 "같은 조건으로
불렀다"를 재실행 없이 증명하고, MP-11은 "그래서 실제로 같은 결과가 나오는가"를 재실행으로
확인한다.

인접하지만 다른 축인 I-05(백테스트=라이브 패리티)는 BT-19
`src/foundation/backtest/application/parity_harness.py::check_parity()`가 담당한다 — PAPER
체결 트레이스와 백테스트 리플레이 체결을 필드별로 대조하는 것으로, "같은 아티팩트/구간을 같은
키로 다시 계산하면 같은 키가 나온다"(BT-9의 결정론)와는 별개로 "실거래와 백테스트가 같은
체결을 낸다"(I-05)를 검증한다. 이 문서의 체인 감사와 병렬적인, 그러나 목적이 다른 축이다.

### 4.1 위조 탐지가 성립하는 이유

- **역상 불가능성**: sha256은 단방향이므로 `key`나 개별 링크 값만 봐서는 원본 소스/데이터/
  설정을 복원할 수 없다 — 공개해도 안전한 것은 그래서 해시뿐이다(`ReproductionKey`가 원문
  대신 이 구조를 publish하는 이유, `domain/visibility.py`: "PROTECTED... publishing the
  reproduction key... in place of source access").
- **애벌런치 효과에 의한 불일치 탐지**: 위조자가 실제로 사용하지 않은 재료로 링크 값을
  조작하면, Tier 2에서 진짜 재료로 재계산한 값과 한 비트도 맞아떨어지지 않는다(근사치가
  존재하지 않는다) — "발산 지점"을 찾을 필요 없이 단순 등치 비교로 충분하다.
- **체인 조립 자체의 위조**: 구성요소는 맞지만 `key`만 다른 값으로 바꿔치기한 경우, Tier 1이
  원재료 없이도 즉시 잡아낸다 — 이것이 구성요소를 다이제스트 하나로 뭉개지 않고 전부 레코드에
  실어 보내는 설계 이유다.

## 5. 결정 기록 — ZK 실행 미채택 (SRC-2, ADR-2026-09-26-B)

**ZK(영지식) 실행은 매매 경로에 채택하지 않는다. 사후 감사 증명 후보로만 기록한다.**

실행 결과 증명은 위 §1~4의 `ReproductionKey` 해시체인(전부 sha256 기반, O(입력 크기)의 해시
연산)으로 이미 충분하다 — Tier 1·2는 검증자가 재료를 확보한 경우 위조를 등치 비교만으로
탐지하며, 별도의 succinct proof 생성/검증 인프라(zk-SNARK/STARK 회로, trusted setup, proving
key 등)를 요구하지 않는다. 이 코드베이스에는 그런 인프라가 존재하지 않으며(위 grep 결과, 이
ADR 결정 문장 한 줄 외에 ZK 관련 코드/설계 문서가 없음), 이 문서는 그것을 만들지 않는다.

ZK 실행 증명(전략 실행 트레이스 전체를 영지식 회로로 증명)이 매매 경로에서 채택되지 않는
이유는 이 ADR의 다른 결정들과 결이 같다: SBX-2가 백테스트/전략 평가에 wall-clock·RSS 상한을
거는 것과 마찬가지로, 매매 경로(및 그와 인접한 백테스트 워커)는 지연에 민감한 자원 예산 하에
있다 — 해시체인 검증은 이미 계산된 값들의 sha256 재계산과 등치 비교뿐이라 그 예산 안에서
자명하게 저렴하지만, 실행 트레이스 전체에 대한 ZK 증명 생성은 원 실행보다 수 자릿수 무거운
연산(회로 컴파일·증명 생성)이 되는 것이 일반적이며, 이 워크로드를 정당화할 요구사항(신뢰할 수
없는 제3자에게 원문을 노출하지 않고 실행을 검증해야 하는 시나리오)이 현재 스펙/ADR 어디에도
없다. 따라서 ZK는 "지금 채택"이 아니라 "미래에 그런 요구사항이 실제로 생기면 검토할 사후 감사
증명 후보"로만 기록해 둔다 — 이 결정을 뒤집으려면 이 ADR을 Superseded 처리하고 새 ADR을
발행해야 한다(ADR-2026-09-26-B Consequences: "기준선·게이트 완화 없음. 되돌림: 이 ADR
Superseded + 해당 리프 종결").

## 6. 요약

| 질문 | 답 |
|---|---|
| 체인 구성요소는? | `script_hash`(DSL-12) + `data_lineage_hash`(LA-23b) + `rollup_version`(DC-10, 값이 아닌 규칙 버전 태그) + `config_hash`(BT-1) → `reproducibility_key`(BT-9 조립). 계약상 5번째 `model_hash`(AI-11)는 미구현 — 설계만. |
| 계산 순서는? | 하위 3개(script/data/config)는 각자의 리프에서 canonical JSON + sha256으로 독립 계산 → BT-9가 그 값+`rollup_version`을 하나의 payload dict로 묶어 다시 canonical JSON + sha256 → 최종 `key`. |
| 재실행 없이 검증 가능한가? | 가능 — Tier 1(레코드만으로 `key` 재조립·비교), Tier 2(원본 재료로 하위 링크 재계산·비교) 모두 실행/시뮬레이션이 없다. 결과 숫자까지 재현하려면(Tier 3, MP-11) 재실행이 필요하며 이는 체인 밖의 별도 상위 검증이다. |
| 입력을 키로부터 복원할 수 있나? | 불가능 — sha256의 단방향성. |
| 위조는 어떻게 걸리나? | 체인 불일치(등치 비교 실패) — Tier 1은 `key`↔구성요소 불일치, Tier 2는 구성요소↔원재료 불일치를 각각 탐지. |
| ZK는 채택되었나? | 아니오 — 매매 경로 비채택, 사후 감사 증명 후보로만 기록(ADR-2026-09-26-B Decision 2). |
