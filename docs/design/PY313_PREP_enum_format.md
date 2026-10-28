# PY313 준비: `(str, Enum)` 포맷 의존 전수조사

작성일 2026-10-28 · task-20672 · 코드/테스트 변경 없음(조사 문서만).

## 배경

Python 3.11+에서 `class X(str, Enum)`(StrEnum 아님)의 `format()`/f-string은 값(`'a'`)이 아니라 `str()` 결과인 `X.MEMBER`가 된다(3.10은 값). `str()`는 두 버전 모두 `X.MEMBER`. `repr()`·`.value`·`==`·json 직렬화(str 서브클래스)는 영향 없음. 현재 repo 런타임은 3.10.11(`requires-python >=3.10`)이라 3.11+ 동작은 **실행으로 확인하지 못했고** 언어 문서 동작에 근거한다.

## 조사 방법과 한계

- 클래스 수집: `git grep -nE 'class \w+\((str, *\w*Enum|\w*Enum, *str)\)' -- src` → **143개 정의(고유 이름 116개)**. `StrEnum`은 src에 없음. 클래스 본문에 `__str__`/`__format__` 오버라이드는 **없음**(전부 영향권).
- 사용처 수집 A(확정): `f"{...Class...}"`, `str(Class.X)`, `.format(`, 로깅 인자에서 **클래스명이 직접 보이는 줄**을 정적 스캔.
- 사용처 수집 B(후보): 클래스를 import하는 파일에서 `{x.side|status|state|kind|venue|timeframe|order_type|asset_class|...}`를 `.value` 없이 f-string/`str()`에 넣은 줄 약 120건을 스캔 후 수동 검토. 변수의 실제 타입은 정적으로 추적하지 않았으므로 **인스턴스 경유 사용은 '미확인'**이다.
- 보조 확인: `json.dumps(default=str)`, `logger.*(%s, x.attr)`, `str(x.attr)`.

## 결론 요약

- **높음(저장/외부 전송 값에 영향): 확인된 항목 0건.** 확정 사용처(A)는 전부 `.value`를 명시해 3.11 영향이 없다(키·지문·SQL CHECK 생성 포함).
- 후보(B) 중 검토한 것은 대부분 `str` 타입 필드(`venue: str`, `timeframe: str`, `Kind=Literal`)이거나 `!r`(repr, 불변)이거나 DB dict 값(`execution['status']`)이었다.
- **미확인(중간 위험 후보, 변수 타입 미추적)**: 아래 '중간/미확인 후보' 표. 모두 예외·메시지 문자열이며 저장 값은 아니다.
- `json.dumps(default=str)`(`core/eventstore/canonical_json.py:23`, `foundation/evidence/domain/rules.py:45`, `market_data/domain/lineage.py:83`, `bitget/private_ws_mixin.py:132`, `validation/application/run_check.py:119`): `default`는 비직렬화 객체에만 호출되고 str 서브클래스 Enum은 json이 값으로 직접 인코딩하므로 해시 입력은 3.11에서도 동일 — **낮음**(실행 검증 안 함).
- DB/드라이버 경로(asyncpg/SQLAlchemy가 Enum 인자를 어떻게 바인딩하는지)는 이번 조사 범위에서 **미확인**. 3.13 전환 전 실DB 스모크 권장.
- 참고: `tests/unit/core/event_bus/test_policy.py:38-41` 주석('3.11+에서 str()은 값')은 사실과 반대다(`(str, Enum)`의 `str()`은 양 버전 모두 `Class.MEMBER`). 단언이 isinstance뿐이라 통과는 하지만 주석은 오해 소지.

## 높음 항목

없음(확정 0건). 위 '미확인' 항목이 타입 확인 후 저장/전송 경로로 판명되면 높음으로 승격.

## 중간/미확인 후보 (이름 기반, 변수 타입 미확인)

| 파일:줄 | 식 | 용도 | 위험도 |
|---|---|---|---|
| src/foundation/backtest/application/quick_backtest_fill.py:104,183 | `{intent.order_type}` | 예외 메시지(OrderType은 Literal 가능성, 미확인) | 중간(미확인) |
| src/foundation/backtest/domain/fill/order_types.py:43 | `{order_type}` | 예외 메시지 | 중간(미확인) |
| src/foundation/market_data/adapters/postgres_reference_repository.py:243 | `{action.action_type}` | 예외 메시지 | 중간(미확인) |
| src/foundation/market_data/application/record_corporate_action.py:54 | `{action.action_type}` | 예외 메시지 | 중간(미확인) |
| src/foundation/market_data/domain/coverage/gaps.py:70 | `{calendar.venue}` | 예외 메시지 | 중간(미확인) |
| src/foundation/mandates/domain/rules/wash_trade.py:94 | `{side}` | 메시지 | 중간(미확인) |
| src/exchanges/kis/market_data_mixin.py:124 | `'{timeframe}'` | 예외 메시지 | 중간(미확인) |
| src/services/oms/domain/venue_profile.py:96,101,106 | `{profile.venue}` / `{cmd.time_in_force}` | 검증 오류 메시지(venue:str 확인, time_in_force 미확인) | 중간(미확인) |
| src/foundation/risk/application/evaluate_personal_order.py:45 | `{order.exchange}` | 거부 메시지 | 중간(미확인) |
| src/services/oms/application/outbox_writes.py:99 | `{outcome.reason}` | outbox dead 사유(DB 저장). `SendOutcome.reason: str`(dispatch_outcome.py:64) 확인 → 문자열 | 낮음 |
| src/services/order_service/submit.py:89 | `{outcome.reason}` | payload_hash 입력. `GateOutcome.reason` 타입 미확인(gate.py:28 GateOutcome은 Enum이나 reason 필드는 미확인) | 미확인(해시 입력이라 확인 우선순위 높음) |
| src/foundation/paper_control/application/apply_safety_control.py:87 | `{reason}` | command detail 저장. reason 타입 미확인 | 미확인(저장 값) |
| src/core/strategy/indicator_key.py:61 | `@{key.timeframe}` | 지표 키 생성. `timeframe: str | None`(:38) → 문자열 | 낮음 |
| src/foundation/market_data/adapters/storage/tick_parquet.py:66 | `{kind}.parquet` | 경로. `Kind=Literal[...]`(:30) → 문자열 | 낮음 |

## 클래스 표 (143개 정의)

사용처 열은 확정 스캔(A)에서 `src/`에서 발견된 줄. '없음'은 클래스명이 f-string/`str()`/`.format`/로그에 직접 나타나지 않았다는 뜻이며, 인스턴스 경유 사용은 위 후보 표에서 다룬 것 외에는 **미확인**.

| 클래스 | 정의(파일:줄) | 사용처(파일:줄) | 위험도 |
|---|---|---|---|
| HandlerCriticality | src/core/event_bus/policy.py:10 | tests만 | 낮음 — `str()`은 tests/unit/core/event_bus/test_policy.py:41(단언은 isinstance뿐)만 |
| Tier | src/core/indicators/catalog/registry_tiers.py:68 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| NotificationChannel | src/core/notifications/channel_policy.py:16 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| SizingMethod | src/core/portfolio/config.py:36 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RiskOutcome | src/core/risk/decision.py:25 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| GateKind | src/core/risk/decision.py:33 | src/foundation/risk_gate/application/evaluate_pre_submit.py:224, src/foundation/risk_gate/application/recovery_gate.py:188 | 낮음 — `.value` 명시 사용(키/지문 생성, 3.11 영향 없음) |
| LimitScope | src/core/risk/limits.py:39 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| LimitMetric | src/core/risk/limits.py:48 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| BundleState | src/core/risk/policy_bundle.py:38 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| VarMethod | src/core/risk_stats/models.py:11 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CircuitBreakerLevel | src/core/safety/circuit_breaker.py:39 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| DataDistrustLevel | src/core/safety/data_distrust.py:40 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Diagnosis | src/core/safety/split_brain.py:33 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| WatchdogAction | src/core/safety/watchdog.py:48 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ProvenanceStatus | src/data/models/base.py:17 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Currency | src/data/models/base.py:25 | src/db/migrations/versions/4a1d0c0de005_ledger_core.py:203 | 낮음 — 마이그레이션에서 `.value` 명시 |
| AssetClass | src/data/models/base.py:30 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| OptionType | src/data/models/base.py:48 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| MemoryType | src/data/models/memory.py:18 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| FSMState | src/data/models/strategy_fsm.py:14 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| TaskStatus | src/data/models/task.py:15 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| OrderStatus | src/data/models/trading.py:20 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| OrderSide | src/data/models/trading.py:41 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| OrderType | src/data/models/trading.py:46 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| BitgetAccountMode | src/exchanges/bitget/account_mode.py:48 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CircuitState | src/exchanges/common/circuit_breaker.py:16 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ExchangeErrorKind | src/exchanges/common/error_taxonomy.py:17 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| KisAccountType | src/exchanges/kis/rate_profile.py:56 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| KiwoomAccountType | src/exchanges/kiwoom/rate_profile.py:34 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Scope | src/foundation/ai/gateway/domain/token_rules.py:28 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| AllocationErrorCode | src/foundation/allocation/domain/policy.py:53 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| AllocationPolicy | src/foundation/allocation/domain/policy.py:68 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ConditionKind | src/foundation/automation/contracts/v1.py:68 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ActionKind | src/foundation/automation/contracts/v1.py:75 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RuleStatus | src/foundation/automation/contracts/v1.py:82 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| PriceField | src/foundation/automation/contracts/v1.py:88 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ConnectionState | src/foundation/connections/contracts/v1.py:22 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CapabilityScope | src/foundation/connections/contracts/v1.py:31 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ConnectionState | src/foundation/connections/domain/models.py:17 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CapabilityScope | src/foundation/connections/domain/models.py:26 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CredentialClass | src/foundation/connections/domain/models.py:37 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| HealthState | src/foundation/connections/domain/models.py:117 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ProviderResponseClassification | src/foundation/connections/domain/rules.py:87 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| EmsErrorCode | src/foundation/ems/contracts/v1.py:68 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| AlgoKind | src/foundation/ems/contracts/v1.py:85 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| EntityErrorCode | src/foundation/entities/contracts/v1.py:35 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Outcome | src/foundation/evidence/contracts/v1.py:22 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Classification | src/foundation/evidence/contracts/v1.py:28 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Outcome | src/foundation/evidence/domain/models.py:15 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Classification | src/foundation/evidence/domain/models.py:21 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| SizingPolicy | src/foundation/follow/contracts/v1.py:27 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| MirrorRejectionReason | src/foundation/follow/domain/mirror_rules.py:24 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| AccountType | src/foundation/ledger/contracts/v1.py:31 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| UserSub | src/foundation/ledger/contracts/v1.py:39 | src/foundation/ledger/domain/chart_of_accounts.py:66 | 낮음 — `.value` 명시 |
| Side | src/foundation/ledger/contracts/v1.py:46 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| LedgerEventType | src/foundation/ledger/contracts/v1.py:51 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| HoldState | src/foundation/ledger/contracts/v1.py:63 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| HoldEvent | src/foundation/ledger/domain/hold_state.py:20 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ExplainErrorCode | src/foundation/mandates/application/explain.py:35 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| MandateBindingStatus | src/foundation/mandates/application/resolve_binding.py:47 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| MandateRevisionState | src/foundation/mandates/contracts/v1.py:46 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Autonomy | src/foundation/mandates/contracts/v1.py:55 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| PolicyOutcome | src/foundation/mandates/contracts/v1.py:61 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ComplianceVerdict | src/foundation/mandates/contracts/v1.py:127 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| MandateRevisionState | src/foundation/mandates/domain/models.py:15 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Autonomy | src/foundation/mandates/domain/models.py:24 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| PolicyOutcome | src/foundation/mandates/domain/models.py:79 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| BenchmarkKind | src/foundation/mandates/reporting/domain/best_execution.py:29 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Timeframe | src/foundation/market_data/contracts/v1_enums.py:28 | src/db/migrations/versions/4b19195124bb_l2_coverage_venue_timeframe_widen.py:54 | 낮음 — 마이그레이션 `m.value` 명시 |
| Venue | src/foundation/market_data/contracts/v1_enums.py:39 | src/db/migrations/versions/4b19195124bb_l2_coverage_venue_timeframe_widen.py:49 | 낮음 — 마이그레이션 `m.value` 명시 |
| Adjustment | src/foundation/market_data/contracts/v1_enums.py:55 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| SymbolStatus | src/foundation/market_data/contracts/v1_enums.py:60 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| QualityIssueType | src/foundation/market_data/contracts/v1_enums.py:67 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Severity | src/foundation/market_data/contracts/v1_enums.py:80 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Verdict | src/foundation/market_data/contracts/v1_enums.py:86 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| SourceKind | src/foundation/market_data/contracts/v2/candle_lineage.py:27 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| QualityGrade | src/foundation/market_data/contracts/v2/coverage.py:33 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| InstrumentLifecycle | src/foundation/market_data/contracts/v2/instruments.py:106 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| InstrumentKind | src/foundation/market_data/contracts/v2/instruments.py:117 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| OptionRight | src/foundation/market_data/contracts/v2/instruments.py:131 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| SettlementType | src/foundation/market_data/contracts/v2/instruments.py:138 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Aggressor | src/foundation/market_data/contracts/v2/microstructure.py:80 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| GapReason | src/foundation/market_data/domain/coverage/gaps.py:44 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| EntitlementDenialReason | src/foundation/market_data/domain/entitlement/policy.py:96 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| SourceContractTier | src/foundation/market_data/domain/entitlement/source_contract.py:48 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RedistributionScope | src/foundation/market_data/domain/entitlement/source_contract.py:55 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| DataUse | src/foundation/market_data/domain/entitlement/source_contract.py:66 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| SourceContractDenialReason | src/foundation/market_data/domain/entitlement/source_contract.py:140 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Adjustment | src/foundation/market_data/domain/instruments/roll.py:26 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RecordKind | src/foundation/market_data/domain/l2_retention.py:38 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RetentionAction | src/foundation/market_data/domain/l2_retention.py:43 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CoverageQuality | src/foundation/market_data/ports/coverage_repository.py:24 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| DataProviderErrorCode | src/foundation/market_data/ports/provider.py:187 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| DeploymentState | src/foundation/paper_control/contracts/v1.py:18 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| DeploymentState | src/foundation/paper_control/domain/models.py:13 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CredentialClass | src/foundation/paper_control/domain/models.py:24 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CommandOutcome | src/foundation/paper_control/domain/models.py:32 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CommandType | src/foundation/paper_control/domain/models.py:37 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| StatementScope | src/foundation/performance/contracts/v1.py:21 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| StatementState | src/foundation/performance/contracts/v1.py:26 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ValuationState | src/foundation/performance/domain/models.py:21 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| StatementState | src/foundation/performance/domain/models.py:26 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CashflowKind | src/foundation/performance/domain/models.py:32 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| CostMethod | src/foundation/positions/contracts/v1.py:35 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| JournalEntryType | src/foundation/positions/contracts/v1.py:40 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| PositionErrorCode | src/foundation/positions/contracts/v1.py:48 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Classification | src/foundation/reconciliation/contracts/v1.py:19 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Classification | src/foundation/reconciliation/domain/models.py:15 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RunState | src/foundation/reconciliation/domain/models.py:29 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| PromotionBlocker | src/foundation/risk/domain/promotion.py:21 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| PersonalRiskViolation | src/foundation/risk/domain/rules.py:17 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| PersonalNotificationKind | src/foundation/risk/ports/notifier.py:13 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RiskOutcome | src/foundation/risk_gate/contracts/v1.py:22 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| GateKind | src/foundation/risk_gate/contracts/v1.py:30 | src/foundation/risk_gate/application/evaluate_pre_submit.py:224, src/foundation/risk_gate/application/recovery_gate.py:188 | 낮음 — `.value` 명시 사용(키/지문 생성, 3.11 영향 없음) |
| SafetyScope | src/foundation/risk_gate/contracts/v1.py:41 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| SafetyControlState | src/foundation/risk_gate/contracts/v1.py:49 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RiskOutcome | src/foundation/risk_gate/domain/models.py:15 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| GateKind | src/foundation/risk_gate/domain/models.py:23 | src/foundation/risk_gate/application/evaluate_pre_submit.py:224, src/foundation/risk_gate/application/recovery_gate.py:188 | 낮음 — `.value` 명시 사용(키/지문 생성, 3.11 영향 없음) |
| SafetyScope | src/foundation/risk_gate/domain/models.py:39 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| SafetyControlState | src/foundation/risk_gate/domain/models.py:47 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| LimitScope | src/foundation/risk_gate/domain/models.py:130 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| LimitMetric | src/foundation/risk_gate/domain/models.py:145 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| LimitBreachSeverity | src/foundation/risk_gate/domain/models.py:154 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RiskSignalType | src/foundation/risk_gate/domain/models.py:159 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RiskSignalSeverity | src/foundation/risk_gate/domain/models.py:169 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RiskSignalState | src/foundation/risk_gate/domain/models.py:174 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| AssetClass | src/foundation/tax_report/domain/capital_gains.py:19 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ConsentState | src/foundation/trust/contracts/v1.py:24 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| ConsentState | src/foundation/trust/domain/models.py:14 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| TenantKind | src/foundation/trust/domain/models.py:19 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| TenantState | src/foundation/trust/domain/models.py:25 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| MembershipRole | src/foundation/trust/domain/models.py:31 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| MembershipState | src/foundation/trust/domain/models.py:39 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RunState | src/foundation/validation/contracts/v1.py:21 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Outcome | src/foundation/validation/contracts/v1.py:29 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| RunState | src/foundation/validation/domain/models.py:22 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| Outcome | src/foundation/validation/domain/models.py:30 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| OutcomeKind | src/services/oms/application/dispatch_outcome.py:37 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| OcoOutcome | src/services/oms/domain/order_types/oco.py:32 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| OrderEvent | src/services/oms/domain/state_machine.py:35 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| GateOutcome | src/services/order_service/gate.py:28 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| InvestmentGoal | src/services/suitability_questionnaire.py:28 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |
| LiquidityNeed | src/services/suitability_questionnaire.py:33 | 없음(직접 사용 미발견; 인스턴스 경유는 미확인) | 낮음(비교/값 사용 추정) · 인스턴스 경유 미확인 |

## 권고(코드 변경은 별도 task)
- 후보 표의 '미확인' 3건(submit.py:89, apply_safety_control.py:87, 메시지류)의 변수 타입을 확인하고, Enum이면 `.value`로 명시.
- 3.11+ 전환 시 `StrEnum` 도입은 `str()` 동작을 값으로 바꾸므로 의도 변경이다 — 별도 아키텍처 결정 필요.
- 3.11+ 인터프리터에서 `tests/unit`의 해당 디렉터리만 스모크 실행해 실제 동작을 확인(이번 조사는 3.10.11에서만 수행).
