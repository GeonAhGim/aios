# Open Policy Agent (OPA) — 코드/스펙 레벨 분석

작성 목적: AIOS 목표 아키텍처에 이름 붙여진 **"Policy Plane (Deterministic PDP)"** —
`foundation/mandates`(테넌트별 퍼센트 한도)와 `core/risk/engine.py`(플랫폼 전체
절대치 지표, `config/risk_policy.yaml`)를 둘 다 조회해 더 엄격한 쪽을 취하는 단일
결정 지점 — 을 만들 때, Open Policy Agent(OPA)를 채택할 가치가 있는지, 아니면
지금의 순수 Python 합성 지점(mandate ∩ RiskEngine)으로 충분한지를 코드 근거로
판단한다. 배경이 되는 결함: 이전 감사에서 kill-switch 게이트가 코드에는 존재하지만
실행 루프에 배선되지 않는 클래스의 결함이 발견됐다(`pre_submit_gate`가 기본값
`None`이면 조용히 건너뛰는 패턴).

조사 대상(얕은 클론, 커밋 해시는 클론 시점 HEAD):

| 리포지토리 | 로컬 경로 | 커밋 | 라이선스 |
|---|---|---|---|
| open-policy-agent/opa | `scratchpad/ext2/opa` | `25a1d928d6ff43000c428ccfc1970d54afb5494b` (2026-09-02) | Apache-2.0 |

`git log -1`이 보여주는 마지막 커밋은 2026-09-02, 조사일(2026-09-03) 바로 전날 —
활발히 유지보수되는 프로젝트임을 확인한다. `LICENSE` 첫 줄은 `Apache License /
Version 2.0`이다.

AIOS 쪽 대조 대상 (실제 코드, `C:/aios/aios/src`):

- `src/core/risk/decision.py` — `RiskDecision`/`RuleResult` (Pydantic, frozen),
  `RiskOutcome`(ALLOW/DENY/REDUCE/PAUSE/ESCALATE), `GateKind`(PRE_SUBMIT 등)
- `src/foundation/mandates/application/evaluate_policy.py` — `EvaluatePolicy`,
  fingerprint 기반 30초 캐시
- `src/services/order_service/submit.py` — `pre_submit_gate: PreSubmitGate | None
  = None` 배선 지점
- `config/risk_policy.yaml` — VaR, correlation, drawdown, leverage 등 절대치 임계값

---

## 1. Core evaluation model — Rego 정책의 실제 모습과 평가 방식

Rego는 "input을 받아 참/거짓/값을 만드는 규칙들의 집합"이라는 선언형(declarative)
언어다. `docs/docs/http-api-authorization.md` L36-57의 실전 예제:

```rego
package httpapi.authz

subordinates := {"alice": [], "charlie": [], "bob": ["alice"], "betty": ["charlie"]}

default allow := false

allow if {
    input.method == "GET"
    input.path == ["finance", "salary", input.user]
}

allow if {
    some username
    input.method == "GET"
    input.path = ["finance", "salary", username]
    subordinates[input.user][_] == username
}
```

핵심 개념(`docs/docs/philosophy/index.md` L100-137)은 세 가지다.

- **`input`** — 쿼리 시점에 동기적으로 밀어넣는 요청 컨텍스트(base document,
  synchronous). AIOS라면 주문 컨텍스트(exchange, notional, tenant_id 등)가 여기 해당.
- **`data`** — OPA에 비동기적으로 적재된 base document(설정값 등)와, 규칙이
  계산해내는 virtual document(결정 자체)를 모두 가리키는 전역 이름공간. `data.httpapi.authz.allow`처럼 정책 함수 자체도 `data` 아래 주소를 갖는다.
- **decision document** — 쿼리가 반환하는 값. 단일 불리언일 수도, 객체 전체일
  수도 있다(`result := data.httpapi.authz`처럼 패키지 전체를 반환 가능).

Go 임베딩 API(`docs/docs/integration.md` L373-399, `v1/rego/rego.go` L1428,
`L1514`의 `func New(...)`/`func (r *Rego) Eval(...)`)는 다음 패턴을 강제한다.

```go
query, err := rego.New(
    rego.Query("x = data.example.authz.allow"),
    rego.Module("example.rego", module),
    ).PrepareForEval(ctx)
// ...
results, err := query.Eval(ctx, rego.EvalInput(input))
```

즉 OPA는 **Rego라는 별도 언어**로 정책을 작성하고, 그 정책을 **Go 런타임**이
컴파일·평가한다. `rego.New`/`PrepareForEval`/`Eval`은 모두 Go API이며, 여기엔
Python 바인딩이 없다(3절에서 다룰 sidecar/WASM 경로가 우회로다).

Rego의 문법 자체는 (`if`, `some`, `contains`, unification `=`, 부정 `not`,
default 값 등) Datalog 계열의 규칙 기반 언어로, Python 개발자에게 익숙한
명령형 흐름 제어(if/else, for 루프, 예외)와는 패러다임이 다르다. `some username`
같은 존재 한정(existential quantification), 규칙이 여러 번 정의되면 OR로
합쳐지는 방식(`allow if { ... }`을 두 번 작성 = OR), `default allow := false`가
없으면 미정의(undefined)가 거짓과 다르게 취급되는 점 등은 실수하기 쉬운 지점이고
숙련에 시간이 든다. `docs/docs/policy-testing.md`와 `docs/docs/style-guide.md`가
별도 학습 자료로 존재한다는 사실 자체가 "Rego는 그 자체로 습득 대상"이라는
방증이다.

**AIOS 시사점**: Rego는 Python이 아니며, OPA를 라이브러리로 직접 embed하려면
**Go 툴체인과 Go API**가 필요하다. Python-only 팀이 정책 로직을 Rego로 재작성하는
것은 "새 언어 하나를 프로덕션 안전 경로에 들이는" 결정이고, 이는 (a) Rego 문법
학습, (b) `opa` 바이너리/Go 빌드 파이프라인 유지, (c) 디버깅 시 Rego 평가
트레이스 해독이라는 3중 비용을 수반한다. 반면 AIOS의 현재 방식(순수 Python 함수가
ALLOW/DENY/REDUCE/PAUSE/ESCALATE를 반환)은 팀이 이미 아는 언어로 동일한 표현력을
제공한다 — 단, "두 레이어의 교집합을 강제하는 단일 지점"이라는 구조적 보장은
Rego 여부와 무관하게 별도로 설계해야 한다(이 점은 5절/최종 결론에서 재론).

---

## 2. Decision logging / audit — OPA decision log vs AIOS RiskDecision

OPA의 decision log는 `docs/docs/management-decision-logs.md`에 정의된 표준
포맷이다. 예시 이벤트(L34-58):

```json
{
  "labels": {"app": "my-example-app", "id": "1780d507-...", "version": "..."},
  "decision_id": "4ca636c1-55e4-417a-b1d8-4aceb67960d1",
  "bundles": {"authz": {"revision": "W3sibCI6InN5cy9jYXRhbG9nIiwicyI6NDA3MX1d"}},
  "path": "http/example/authz/allow",
  "input": {"method": "GET", "path": "/salary/bob"},
  "result": "true",
  "requested_by": "[::1]:59943",
  "timestamp": "2018-01-01T00:00:00.000000Z"
}
```

필드 표(L63-84)에는 `decision_id`, `trace_id`/`span_id`(W3C trace-context 호환),
`bundles[_].revision`, `input`, `result`, `timestamp`, `metrics`, `erased`/`masked`
(민감정보 마스킹 결과)가 있다. "OPA server 모드로 실행하고 decision logging을
켜면 이 이벤트가 자동 생성된다"는 것이 핵심 가치 제안이다 — **정책 평가 자체가
감사 로그를 만든다.**

이걸 AIOS의 `src/core/risk/decision.py`(L65-85)의 `RiskDecision`과 나란히 놓으면:

```python
class RiskDecision(BaseModel, frozen=True):
    schema_version: Literal["v1"] = SCHEMA_VERSION
    decision_id: UUID
    gate_kind: GateKind
    tenant_id: UUID
    outcome: RiskOutcome
    reason_codes: tuple[str, ...]
    rule_results: tuple[RuleResult, ...]
    rule_hash: str
    engine_version: str
    inputs_hash: str
    evaluated_at: datetime
    expires_at: datetime
    trace_id: UUID
    evidence_ref: str | None
    latency_us: int
```

두 스키마는 개념적으로 거의 1:1 대응한다(`decision_id`↔`decision_id`,
`bundles[_].revision`↔`rule_hash`/`engine_version`, `input`↔`inputs_hash`,
`trace_id`↔`trace_id`, `timestamp`↔`evaluated_at`). 오히려 AIOS 쪽이 두 가지를
**이미 앞서 있다**: (1) `rule_hash`+`inputs_hash`+`engine_version`의 조합은
"이 결정이 정확히 어떤 규칙 버전·입력 해시로 재현 가능한가"를 보장하는 재생
(replay) 계약인데, OPA decision log의 `bundles[_].revision`은 번들 버전만 기록할
뿐 입력 해시는 별도 필드가 없다. (2) `RuleResult.missing_fields`에
`model_validator`로 "판단 불가면 반드시 DENY"를 코드 레벨로 강제하는 fail-closed
불변식은, OPA에서는 정책 작성자가 Rego로 직접 짜 넣어야 하는 관례이지 프레임워크가
타입 시스템으로 강제해주지 않는다.

OPA가 주는 것은 "결정 로깅을 잊어버릴 수 없게 만드는 표준화된 파이프라인"이지,
AIOS가 이미 갖고 있지 않은 **새로운 감사 능력**이 아니다. `management-decision-logs.md`
L115-126의 로컬 콘솔 로깅, L128-263의 마스킹 규칙(`system.log.mask`), L265-298의
드롭 규칙(`system.log.drop`)은 모두 "정책으로 로깅 정책 자체를 제어"하는 메타
계층인데, 이는 강력하지만 AIOS의 P0-R5("Authority Wiring Proof")가 요구하는 것 —
"게이트가 실행 경로에 실제로 배선되어 매 실행마다 호출됐다"는 증거 — 을 자동으로
만들어주지는 않는다. OPA decision log도 어디까지나 **OPA가 호출됐을 때만** 남는
로그이고, "PDP가 호출 자체를 건너뛰었는가"를 증명하려면 여전히 별도의 배선 증명
(호출 그래프 정적 분석, 통합 테스트에서 게이트 부재 시 실패)이 필요하다 —
이는 OPA를 쓰든 안 쓰든 AIOS가 스스로 만들어야 하는 증거다.

**AIOS 시사점**: OPA decision log의 필드 설계는 AIOS `RiskDecision`이 이미
구현한 것과 거의 동형(isomorphic)이며, 일부는 AIOS 쪽이 더 엄격하다(재현성 해시
3종 세트). OPA를 채택해도 "결정이 로깅됐다"는 증거의 질이 유의미하게 좋아지지
않는다 — 오히려 두 개의 병렬 로그 포맷(OPA decision log + AIOS RiskDecision)을
유지·정합시키는 비용만 늘어난다. P0-R5가 요구하는 "배선 증명"은 로그 포맷의
문제가 아니라 **호출 그래프의 문제**이므로, OPA 도입이 이 P0을 직접 해결해주지
않는다.

---

## 3. Embedding models — Go 라이브러리 vs sidecar vs WASM

OPA는 세 가지 임베딩 모델을 제공한다.

**(a) Go 라이브러리 직접 임베딩.** `v1/rego/rego.go`(전체가 `rego` 패키지)를
`import "github.com/open-policy-agent/opa/v1/rego"`로 링크. `docs/docs/integration.md`
L344-348: "Use the low-level ... package to embed OPA as a library inside services
**written in Go**". Python 서비스에서는 이 경로를 쓸 수 없다 — Go 프로세스를
별도로 띄우거나 cgo/서브프로세스 브리지가 필요하다.

**(b) Sidecar/서버 프로세스.** `server/` 패키지(`server/server.go`,
`server/handlers/`)가 구현하는 REST API. 클라이언트(Python 포함 아무 언어나)는
HTTP로 쿼리한다(`docs/docs/rest-api.md` L1016-1018):

```http
POST /v1/data/example/allow HTTP/1.1
Content-Type: application/json

{"input": {"user": "alice"}}
```

`docs/docs/http-api-authorization.md` L135-157의 Python 클라이언트 예시가 정확히
AIOS가 채택할 모양이다 — `requests.post("http://127.0.0.1:8181/v1/data/...", json=...)`.
이 경로는 Python 팀에게 가장 자연스럽지만, **매 pre-trade 결정마다 로컬 네트워크
홉 하나(localhost sidecar라 해도 TCP/Unix socket + JSON 직렬화)가 추가**된다.
`docs/docs/policy-performance.md` L8-9가 스스로 명시하는 기준선: "a microservice
API authorization decision might have a budget in the order of **1 millisecond**".
즉 OPA 자신도 저지연 유스케이스를 별도로 챙겨야 하는 특수 케이스로 취급한다 —
sidecar HTTP 왕복 비용이 예산의 상당 부분을 먹을 수 있다는 뜻이다.

**(c) WASM 컴파일.** `opa build -t wasm -e example/allow example.rego`
(`docs/docs/wasm.md` L37-39)로 Rego를 WASM 모듈로 컴파일해 Go 의존성 없이
평가할 수 있다. 그러나 "From Scratch" 통합 섹션(L94-344)이 보여주듯, 이는
**저수준 ABI**다 — `opa_malloc`/`opa_json_parse`/`opa_eval_ctx_set_input`/
`opa_heap_ptr_set` 등을 직접 호출하며 메모리 관리(heap stash/restore)까지
호스트가 책임져야 한다(L253-344). 공식적으로 지원되는 것은 JavaScript SDK뿐이고
(L78-86), Python에서 쓰려면 `wasmtime-py` 같은 커뮤니티 WASM 런타임 위에 이 ABI
전체를 재구현해야 한다 — 이는 "라이브러리 하나 pip install"이 아니라 **자체
WASM 바인딩 계층을 만드는 프로젝트**다. 또한 `http.send` 같은 빌트인은 WASM에서
기본 미지원이라(L24-27) 호스트가 대신 구현해야 한다.

세 경로를 latency/운영 비용 축으로 정리하면:

| 모델 | Python에서 실현 가능성 | 지연 비용 | 운영 비용 |
|---|---|---|---|
| (a) Go 라이브러리 | 불가 (Go 전용) | 없음(인프로세스) | Go 빌드 체인 추가 |
| (b) Sidecar HTTP | 가능, 가장 쉬움 | 네트워크 왕복 1회 + JSON 직렬화, 1ms 예산 잠식 | 프로세스 하나 추가 배포/헬스체크/버전 관리 |
| (c) WASM | 가능하나 저수준 ABI 직접 구현 필요 | 인프로세스(빠름) | Python↔WASM 바인딩 자체 개발·유지보수 |

**AIOS 시사점**: 저지연 pre-trade 주문 경로에 OPA를 넣는다면 현실적 선택지는
(b) sidecar뿐이다(WASM은 바인딩 개발 비용이 너무 크고, Go 임베딩은 스택 불일치로
배제). 그런데 sidecar는 정확히 AIOS가 피하고 싶어 하는 것 — "매 주문마다 강제
네트워크 홉"을 도입한다. 이는 OPA 공식 문서가 스스로 인정하는 1ms 예산 논의와
정면으로 부딪힌다. AIOS의 현재 인프로세스 Python 함수 호출(mandate 조회 + risk
engine 평가)은 네트워크 홉이 전혀 없으므로, 지연 관점에서는 OPA sidecar보다
구조적으로 유리하다.

---

## 4. Policy testing & bundle distribution — `opa test`, 서명, 버저닝

`opa test`는 Rego로 Rego를 테스트하는 프레임워크다. `docs/docs/policy-testing.md`
L21-57의 예제:

```rego title="example_test.rego"
package authz_test
import data.authz

test_post_allowed if {
    authz.allow with input as {"path": ["users"], "method": "POST"}
}

test_get_another_user_denied if {
    not authz.allow with input as {"path": ["users", "bob"], "method": "GET", "user_id": "alice"}
}
```

`with input as {...}`로 입력을 오버라이드해 순수 함수처럼 단위 테스트한다는 점은
pytest의 파라미터화 테스트와 개념적으로 동일하다. 실행 결과(L69-76):

```
$ opa test . -v
data.authz_test.test_post_allowed: PASS (1.417µs)
...
PASS: 4/4
```

번들 서명은 `docs/docs/management-bundles/index.md` L479-608에 상세하다. 서명된
번들은 `.signatures.json`을 포함하고(L494-503), 그 안에는 번들 내 각 파일의
SHA 해시를 담은 JWT가 들어간다(L509-534):

```json
{
  "files": [
    {"name": ".manifest", "hash": "c213...", "algorithm": "SHA-256"},
    {"name": "roles/bindings/data.json", "hash": "42cf...", "algorithm": "SHA-256"}
  ]
}
```

OPA는 `--verification-key`(공개키)로 JWT 서명을 검증하고, 그 결과에 따라서만
새 번들을 활성화한다(L483-485, L591-601) — "서명 실패 시 기존 번들 유지"라는
fail-safe 동작이다. 단, 중요한 예외가 명시돼 있다(L488): **`opa eval`, `opa test`
같은 pre-production 서브커맨드는 서명 검증을 하지 않는다.** 검증은 오직 서버
모드에서 번들을 로드할 때만 작동한다.

번들 버저닝은 `.manifest`의 `revision` 필드(L286-288, L323-357)로 이루어지며,
이는 문자열 하나일 뿐 별도의 semver 강제나 호환성 검사 로직은 없다 — "버전
문자열을 무엇으로 채울지는 전적으로 사용자 책임"이다.

**AIOS 시사점**: `opa test`의 "정책을 정책 언어로 테스트한다"는 아이디어와
번들 서명의 "서명 실패 시 롤백하지 않고 기존 상태 유지"라는 fail-safe 설계는
Invariant I-09(단일 합성 지점)와 Strategy Registry의 아티팩트/정책 버저닝
요구에 참고할 가치가 있는 **패턴**이다. 그러나 이는 OPA라는 도구 자체를
채택해야만 얻을 수 있는 게 아니다 — AIOS가 이미 갖고 있는 `rule_hash`/
`engine_version`/pytest 기반 유닛 테스트 스위트에 "서명 실패 시 이전 정책
버전으로 유지"라는 동일한 fail-safe 규칙을 추가하는 것으로 같은 효과를 얻을 수
있다. 그리고 `.signatures.json` 검증이 `opa eval`/`opa test`에서는 작동하지
않는다는 사실은, "정책 파이프라인의 서명 검증"이 도구가 자동으로 주는 안전망이
아니라 여전히 통합 지점을 스스로 설계해야 하는 영역임을 보여준다.

---

## 5. What OPA does NOT give you — 수치/통계 빌트인의 부재

OPA의 산술 빌트인은 `v1/ast/builtins.go`에 명시적으로 나열돼 있다. "Arithmetic"
섹션(L499-629)의 전체 목록은 `plus`, `minus`, `mul`(`*`), `div`(`/`), `round`,
`ceil`, `floor`, `abs`, `rem`(`%`) — 사칙연산과 반올림뿐이다. "Aggregates"
섹션(L775-877)은 `count`, `sum`, `product`, `max`, `min`, `sort` — 컬렉션 위의
단순 집계뿐이다. 예를 들어 `Sum`의 선언(L799-813):

```go
var Sum = &Builtin{
    Name:        "sum",
    Description: "Sums elements of an array or set of numbers.",
    Decl: types.NewFunction(
        types.Args(types.Named("collection", types.NewAny(
            types.SetOfNum, types.NewArray(nil, types.N))).
            Description("the set or array of numbers to sum")),
        types.Named("n", types.N).Description("the sum of all elements"),
    ),
    Categories:  aggregates,
    CanSkipBctx: true,
}
```

`grep`으로 전체 빌트인 카탈로그(`docs/docs/policy-reference/builtins/*.mdx`,
`numbers.mdx`, `aggregates.mdx` 등)를 확인해도 평균(mean)/표준편차/분산/백분위수
/상관계수/공분산 같은 통계 함수는 존재하지 않는다. `numbers.range`/
`numbers.range_step`(L1498, L1511)조차 "정수 구간 배열 생성" 유틸일 뿐이다.
JWT 처리(`io.jwt.*`), 정규식(`regex.*`), 암호화(`crypto.*`), HTTP(`http.send`)
같은 풍부한 빌트인은 있지만, 이들은 모두 **인가/신원/데이터 조회** 도메인이지
금융 리스크 계산 도메인이 아니다.

이는 설계 철학과 일치한다 — OPA는 "누가 무엇을 할 수 있는가"를 결정하는 범용
정책 엔진이지, 도메인 특화 계산기가 아니다. AIOS의 `config/risk_policy.yaml`이
요구하는 VaR(`var.confidence: 0.95`, `var.horizon_days: 1`), 상관관계
(`correlation_risk.threshold: 0.7`), 최대 손실폭(`max_drawdown.hard_stop_pct`),
레버리지 배수 조정(`leverage.coverage_multiplier`) 같은 계산은 시계열 데이터,
공분산 행렬, 반복 시뮬레이션을 필요로 하며, 이런 계산을 Rego로 처음부터 다시
구현하는 것은 사실상 불가능하거나(Rego는 반복문·가변 상태가 없는 선언형 언어라
수치 알고리즘 구현에 부적합) 매우 비효율적이다.

**AIOS 시사점**: `core/risk/engine.py`의 9개 지표 평가기는 OPA를 도입해도
**그대로 존재해야 한다** — OPA는 이를 대체할 수 없다. OPA가 맡을 수 있는 역할이
있다면 그것은 "RiskEngine과 mandates가 이미 계산해 낸 사실(fact)들을 `input`으로
받아, 두 레이어의 판정을 어떻게 합성할지(예: '더 엄격한 쪽 채택', '어느 게이트가
우선순위를 갖는가')를 규칙으로 표현하는 최종 합성 계층"뿐이다. 즉 OPA를 쓰더라도
아키텍처는 "RiskEngine(Python, 수치 계산) + Mandates(Python, 퍼센트 한도) → OPA
(Rego, 최종 ALLOW/DENY 합성)"의 3단 구조가 되며, 이는 AIOS가 이미 구상한
"mandate ∩ RiskEngine" 합성 지점을 Python으로 직접 구현하는 것과 **입력·출력이
동일**하다 — 차이는 그 합성 로직을 Python으로 쓰느냐 Rego로 쓰느냐뿐이다.

---

## 최종 결론

**AIOS Policy Plane의 기반으로 OPA를 채택할 가치가 있는가, 아니면 지금의 순수
Python 합성 지점(mandate ∩ RiskEngine)으로 충분한가?**

**결론: 지금의 순수 Python 합성 지점으로 충분하며, OPA 도입은 이 시점에서
정당화되지 않는다.** 근거는 다음 네 가지다.

1. **팀 스킬셋 불일치가 구조적이다.** OPA를 "제대로" 쓰는 유일한 경로(1절, 3절
   (a))는 Go 언어 임베딩이다. Python-only 팀에게 남는 것은 sidecar(HTTP 홉
   추가, 3절 (b)) 아니면 WASM 저수준 ABI 직접 구현(3절 (c))뿐이며, 둘 다 "정책
   엔진 하나 들여왔더니 그걸 통합하는 프로젝트가 하나 더 생기는" 역설에 빠진다.
   게다가 정책 자체도 Rego라는 새 언어로 다시 작성해야 한다 — 이는 순수 리스크
   합성 로직 자체보다 큰 학습·유지보수 비용이다.

2. **결정 로깅/감사는 이미 동급 이상으로 해결돼 있다.** 2절에서 확인했듯
   AIOS의 `RiskDecision`(`rule_hash`+`inputs_hash`+`engine_version`+`trace_id`+
   `evidence_ref`)은 OPA decision log가 주는 필드 집합과 동형이며, 재현성
   보장 측면에서는 오히려 더 엄격하다. OPA를 들여도 P0-R5("Authority Wiring
   Proof")가 요구하는 "게이트가 실행 경로에 실제로 배선됐다"는 증거는 자동으로
   따라오지 않는다 — 이는 호출 그래프 검증의 문제이지 로그 포맷의 문제가
   아니기 때문이다.

3. **저지연 요구와 정면충돌한다.** OPA 공식 문서 스스로 "1ms 예산"을 저지연
   유스케이스의 기준선으로 제시하며(3절), Python에서 현실적인 유일한 통합
   경로인 sidecar는 매 pre-trade 결정마다 네트워크 홉을 추가한다. AIOS의 현재
   인프로세스 Python 함수 호출에는 이 비용이 없다.

4. **도메인 계산 능력은 어차피 이전되지 않는다.** 5절에서 확인했듯 OPA에는
   VaR/상관관계/드로다운 같은 수치·통계 빌트인이 전혀 없다. 그 결과 OPA를
   채택해도 `core/risk/engine.py`의 9개 지표 평가기는 그대로 남아야 하고, OPA는
   "이미 계산된 사실을 입력받아 최종 ALLOW/DENY를 합성하는" 얇은 계층 하나만
   추가로 얹는 역할만 할 수 있다. 그런데 그 역할 — "두 레이어의 결과를 받아
   더 엄격한 쪽을 채택하는 단일 합성 함수" — 은 Python으로 20~30줄짜리 순수
   함수로 구현 가능한 문제이지, 별도의 정책 언어·런타임·번들 배포 시스템을
   정당화할 만큼 복잡한 문제가 아니다.

다만 OPA 코드베이스에서 **패턴으로서 가져올 가치가 있는 것**은 분명히 있다
(4절): (i) "정책을 정책 언어로/정책과 같은 방식으로 테스트한다"는 `opa test`의
발상은 AIOS의 리스크 규칙 pytest 스위트에 그대로 적용 가능하고, (ii) "서명
검증 실패 시 새 정책을 활성화하지 않고 기존 정책을 유지한다"는 번들 서명의
fail-safe 원칙은 Strategy Registry/정책 버저닝에 이식할 가치가 있다. 그러나
이런 원칙을 얻기 위해 OPA 런타임 전체, Rego 언어, Go 빌드 체인, sidecar
프로세스를 들일 필요는 없다 — **원칙만 차용하고 도구는 차용하지 않는다**는
것이 Sigstore/in-toto 분석(`ext_sigstore_intoto_slsa.md`)에서 도달한 결론과
같은 종류의 판단이다.

요약하면, Policy Plane은 **"mandate ∩ RiskEngine을 순수 Python 함수로 합성하고,
그 함수 호출이 실행 루프에서 실제로 우회 불가능하게 배선됐는지를 정적 분석·
통합 테스트로 증명하는"** 방향으로 가야 한다. OPA는 "범용 인가 결정을 여러
마이크로서비스에 정책으로 배포해야 하는" 조직(HTTP API 인가, 쿠버네티스
admission control 등)에는 강력한 도구이지만, AIOS처럼 (a) 단일 스택(Python),
(b) 저지연 단일 경로(pre-trade 주문), (c) 이미 존재하는 강력한 도메인 계산
엔진(RiskEngine)을 가진 상황에는 과잉 설계(over-engineering)다.
