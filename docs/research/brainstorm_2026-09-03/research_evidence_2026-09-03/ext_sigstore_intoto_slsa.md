# Sigstore/Cosign · in-toto Attestation · SLSA — 코드/스펙 레벨 분석

작성 목적: AIOS 목표 아키텍처의 **Strategy Registry / Artifact Trust Plane**(`AIOS_Target_Architecture_Freeze_v0.1_2026-09-03.md` §1 4번, §3 데이터 흐름 `Strategy Registry/Artifact Trust Plane ──(artifact_hash)──> Validation & Experiment Plane`)이 서명·provenance·attestation을 추가할 때, Sigstore/in-toto/SLSA의 **표준과 도구를 그대로 채택**할지 아니면 AIOS 자체 해시 체인에 서명 필드 하나만 얹을지를 코드/스펙 근거로 판단한다.

조사 대상(얕은 클론, 커밋 해시는 클론 시점 HEAD):

| 리포지토리 | 로컬 경로 |
|---|---|
| sigstore/cosign | `scratchpad/ext2/cosign` |
| in-toto/attestation | `scratchpad/ext2/in-toto-attestation` |
| slsa-framework/slsa | `scratchpad/ext2/slsa` |
| in-toto/in-toto (Python 레퍼런스 구현) | `scratchpad/ext2/in-toto-python` |

AIOS 쪽 대조 대상: `src/contracts/enterprise.py`(`StrategyPackage.artifact.content_hash`, `risk_envelope`, `hypothesis`, `validation_refs`, `license_ref` — 죽은 계약, API 미배선), `src/services/paper_strategy_projection.py`(위 계약을 채우는 유일한 프로젝션 코드), `src/foundation/validation/domain/rules.py`(`compute_input_snapshot_hash`, `compute_result_hash` — 전부 sha256(canonical JSON)), `src/core/security/key_ring.py`(대칭키 AES-256 `KeyRing`, PAPER/LIVE 키 분리, **비대칭 서명 키가 아님**).

---

## 1. in-toto Attestation — Statement 포맷

`spec/v1/statement.md`가 정의하는 in-toto Attestation의 중간 계층. AIOS가 기존 `artifact_hash`를 감쌀 봉투(envelope)의 최소 단위다.

```jsonc
// spec/v1/statement.md L9-22
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    { "name": "<NAME>", "digest": {"<ALGORITHM>": "<HEX_VALUE>"} },
    ...
  ],
  "predicateType": "<URI>",
  "predicate": { ... }
}
```

`subject`는 `ResourceDescriptor` 배열이며(`spec/v1/resource_descriptor.md` L8-22), 필드 중 `uri`/`digest`/`content` 셋 중 최소 하나만 있으면 되지만 Statement의 `subject`는 "Each element MUST have `digest` set"(statement.md L37)이라는 별도 강제 규칙이 있다. digest는 알고리즘→hex 맵(`DigestSet`)이므로 AIOS의 `sha256:<64hex>` 포맷을 `{"sha256": "<64hex>"}`로 바꾸기만 하면 그대로 들어간다.

AIOS `StrategyPackage.artifact`를 Statement로 감싼 실전 예시(값은 예시용 해시):

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    { "name": "strategy-package", "digest": { "sha256": "8f14e45fceea167a5a36dedd4bea2543c9b8a2b7b5d1e4a0f8c2e0b1a2c3d4e5" } }
  ],
  "predicateType": "https://slsa.dev/provenance/v1",
  "predicate": { "...": "SLSA Provenance predicate, 2절 참조" }
}
```

`subject[0].digest`는 `StrategyArtifact.content_hash`(enterprise.py L121-125, `sha256:[A-Fa-f0-9]{64}` 패턴)를 콜론으로 쪼갠 값과 정확히 일치한다 — 필드명만 다르지 내용은 AIOS가 이미 계산해 두고 있는 값이다. `predicateType`는 URI로 어떤 종류의 주장인지를 식별하고, `predicate`는 그 스키마의 실제 payload다. 서명은 이 레이어의 책임이 아니라 바깥의 **Envelope** 레이어(`spec/v1/envelope.md`) 책임이며, 권장 포맷은 DSSE다: `signatures`(필수, 배열 — 다중 서명 지원), `payload`(base64 인코딩된 Statement JSON), `payloadType`(`application/vnd.in-toto+json` 등)(envelope.md L26-58 요약). Envelope 스펙은 "Sigstore Bundle은 단일 서명만 지원하므로 ITE-5 미준수"(envelope.md L21-23)라고 명시한다 — 즉 in-toto Statement/Envelope와 Sigstore Bundle은 같은 계열이지만 동일한 스펙이 아니다. AIOS가 in-toto Statement를 채택해도 반드시 Sigstore Bundle 포맷으로 저장해야 하는 것은 아니며, DSSE JSON 파일 하나로 자체 저장소에 넣어도 스펙 준수다.

**AIOS 시사점**: Statement 포맷은 AIOS `StrategyPackage.artifact.content_hash`를 감싸는 봉투로 거의 마찰 없이 채택 가능하다 — 새 필드를 추가하는 게 아니라 기존 `sha256:<hex>` 문자열을 `{"sha256": "<hex>"}` 맵으로 재포맷하고 `predicateType` URI 하나만 붙이면 된다. 반대로 서명(Envelope)까지 Sigstore/DSSE 표준 라이브러리로 처리할지는 3절의 쟁점과 별개로 결정 가능 — **Statement 데이터 모델 차용과 Envelope/서명 도구 채택은 분리 가능한 두 개의 결정**이라는 점이 핵심이다.

---

## 2. SLSA Provenance predicate

파일: `spec/provenance.md`, `spec/build-provenance.md`, `spec/schema/provenance.cue`. (클론된 slsa 리포는 버전 디렉터리 없이 `spec/` 최상위에 v1.0 이후 최신 초안이 있다 — `predicateType: https://slsa.dev/provenance/v1`.)

전체 predicate 스키마(`spec/schema/provenance.cue` 전문, 컴팩트하므로 전체 인용):

```javascript
// spec/schema/provenance.cue L1-49
{
    "_type": "https://in-toto.io/Statement/v1",
    "subject": [...],
    "predicateType": "https://slsa.dev/provenance/v1",
    "predicate": {
        "buildDefinition": {
            "buildType": string,
            "externalParameters": object,
            "internalParameters": object,
            "resolvedDependencies": [ ...#ResourceDescriptor ],
        },
        "runDetails": {
            "builder": {
                "id": string,
                "builderDependencies": [ ...#ResourceDescriptor ],
                "version": { ...string },
            },
            "metadata": {
                "invocationId": string,
                "startedOn": #Timestamp,
                "finishedOn": #Timestamp,
            },
            "byproducts": [ ...#ResourceDescriptor ],
        }
    }
}
#ResourceDescriptor: {
    "uri": string,
    "digest": { "sha256": string, "sha512": string, "gitCommit": string, [string]: string },
    "name": string, "downloadLocation": string, "mediaType": string,
    "content": bytes, // base64-encoded
    "annotations": { [string]: _ }
}
#Timestamp: string  // <YYYY>-<MM>-<DD>T<hh>:<mm>:<ss>Z
```

핵심 필드 의미(`build-provenance.md` L316-409): `builder.id` — "URI indicating the transitive closure of the trusted build platform. This is intended to be the sole determiner of the SLSA Build level."(L353-355), 즉 **누가 빌드를 실행했는가**의 신뢰 루트. `buildType` — "Identifies the template for how to perform the build and interpret the parameters and dependencies."(L184-185). `externalParameters` vs `internalParameters` — "externalParameters: the external interface to the build. ... MUST be verified downstream" vs "internalParameters: set internally by the platform. ... OPTIONAL and need not be verified"(L73-80), 외부(신뢰 안 함) vs 내부(플랫폼이 이미 신뢰) 입력 구분. `resolvedDependencies` — 빌드 시점에 실제로 fetch된 의존성의 digest 목록.

### AIOS `strategy_validation_bundle`(artifact_hash + policy_hash + 6개 check result_hash)에 매핑

| SLSA Provenance 필드 | AIOS 대응(가상 매핑) | 정합성 |
|---|---|---|
| `subject[].digest` | `artifact_hash` | 자연스러움 — 결과물의 정체성 |
| `buildDefinition.externalParameters` | 전략 IR/파라미터 | 어느 정도 자연스러움 |
| `buildDefinition.resolvedDependencies` | 6개 체크의 `result_hash`? 시장 데이터 스냅샷? | **애매함** — "빌드 시점 fetch 입력"이지 "빌드 후 검증 결과"가 아님 |
| `runDetails.builder.id` | 검증 파이프라인 버전/인스턴스 ID | 억지로 가능하나 "builder" 은유가 안 맞음 |
| `runDetails.metadata.invocationId/startedOn/finishedOn` | `ValidationRun.id`, `created_at`/`completed_at` | 자연스러움 |
| 6개 체크의 PASS/FAIL/PASS_WITH_OBLIGATIONS 판정 | ??? | **대응 필드 없음** |
| `policy_hash`(정책 버전 고정) | ??? | **대응 필드 없음** |

**억지 유비인 이유**: SLSA Provenance의 모델은 "빌드 플랫폼이 소스로부터 결정론적으로 산출물을 생성하는 과정"(`build-provenance.md` L42-43: "Provenance is an attestation that a particular build platform produced a set of software artifacts through execution of the `buildDefinition`.")을 기술하도록 설계됐다. `resolvedDependencies`, `externalParameters`/`internalParameters` 구분은 전부 "빌드 입력의 신뢰 경계"를 다루는 필드다. 그런데 AIOS의 `strategy_validation_bundle`이 담아야 하는 것은 빌드가 아니라 **사후 정책 판정**이다 — "이 전략은 이미 만들어졌고(artifact_hash로 식별됨), 그 다음 6개의 독립적인 체크(백테스트, OOS, robustness 등)가 어떤 정책 버전(`policy_hash`)에 대해 PASS/FAIL/PASS_WITH_OBLIGATIONS를 냈는가"다. 이건 SLSA Provenance가 아니라 SLSA의 다른 predicate인 **VSA(Verification Summary Attestation)**나 in-toto의 범용 커스텀 predicate에 훨씬 가까운 형태다 — 6개 체크 각각을 `subject`가 여러 개인 Statement로 표현하거나, `predicate.results: [{check_type, outcome, result_hash}, ...]` 같은 AIOS 전용 predicateType을 새로 정의하는 편이 SLSA Provenance를 억지로 채우는 것보다 스키마가 훨씬 덜 왜곡된다.

**AIOS 시사점**: SLSA Provenance predicate 자체(`buildDefinition`/`runDetails`)는 "전략 코드가 어떤 빌드 환경에서 컴파일됐는가"(`StrategyArtifact.build_environment_ref`) 파트에는 잘 맞지만, "6개 체크가 검증 정책에 대해 어떤 판정을 냈는가" 파트에는 억지 유비다. 채택한다면 **두 개의 서로 다른 predicateType**으로 쪼개는 게 맞다 — (a) `https://slsa.dev/provenance/v1`로 아티팩트 빌드 이력을, (b) AIOS 커스텀 predicateType(`https://aios.dev/predicates/validation-bundle/v1` 같은 것)으로 `policy_hash` + 6개 `result_hash` + `Outcome`을 표현. in-toto Statement 봉투는 공유하되 predicate 내용은 SLSA를 그대로 베끼지 않는 것이 스키마 정합성 측면에서 더 정직하다.

---

## 3. Sigstore/Cosign 서명 흐름 — keyless(Fulcio+Rekor) vs 키 기반

### 3-1. keyless 흐름 (기본값, README 인용)

```
README.md L90-107 (cosign sign $IMAGE, 축약)
 cosign sign $IMAGE
Generating ephemeral keys...
Retrieving signed certificate...
	Note that there may be personally identifiable information associated with this signed artifact.
	This may include the email address associated with the account with which you authenticate.
	This information will be used for signing this artifact and will be stored in public transparency logs and cannot be removed later.
By typing 'y', you attest that you grant (or have permission to grant) and agree to have this information stored permanently in transparency logs.
Are you sure you would like to continue? [y/N] y
...
Successfully verified SCT...
tlog entry created with index: 12086900
Pushing signature to: $IMAGE
```

README.md L109-112가 흐름을 요약한다: "cosign will request a code signing certificate from the Fulcio certificate authority. The subject of the certificate will match the email address you logged in with. Cosign will then store the signature and certificate in the Rekor transparency log..." 즉 keyless = OIDC 로그인(이메일 등 신원) → Fulcio가 그 신원을 담은 **단기(수분) 인증서** 발급 → 그 인증서의 개인키로 서명 → 서명+인증서를 **Rekor(공개 append-only 로그)**에 영구 기록. README.md L98-100이 명시하듯 이건 경고 문구가 따로 있을 만큼 비가역적 공개 행위다.

### 3-2. 검증 코드 경로 (`pkg/cosign/verify.go`)

`ValidateAndUnpackCertWithIntermediates`(verify.go L372-463)가 인증서 체인 검증의 핵심이다:

```go
// verify.go L396-406
var chains [][]*x509.Certificate
if co.TrustedMaterial != nil {
    if chains, err = verify.VerifyLeafCertificate(cert.NotBefore, cert, co.TrustedMaterial); err != nil {
        return nil, nil, err
    }
} else {
    // If the trusted root is not available, use the verifiers from cosign (legacy).
    chains, err = TrustedCert(cert, co.RootCerts, intermediateCerts)
    if err != nil { return nil, nil, err }
}
```

이어서 `CheckCertificatePolicy`(발급자/subject 매칭, L414-417), SCT(Signed Certificate Timestamp, CT 증명) 검증(L419-460)까지 체인으로 확인한다. 신원 검증은 (1) Fulcio 루트까지의 X.509 체인, (2) SCT를 통한 "이 인증서가 CT 로그에 실제로 올라갔다"는 증명, 두 단계로 이뤄진다. Rekor 포함 증명(inclusion proof)은 별도로 `VerifyBundle`(verify.go L1286-1366)에서 확인한다:

```go
// verify.go L1333-1348
if co.TrustedMaterial != nil {
    payload := bundle.Payload
    logID, err := hex.DecodeString(payload.LogID)
    entry, err := tlog.NewEntry(body, payload.IntegratedTime, payload.LogIndex, logID, bundle.SignedEntryTimestamp, nil)
    if err := tlog.VerifySET(entry, co.TrustedMaterial.RekorLogs()); err != nil {
        return false, fmt.Errorf("verifying bundle with trusted root: %w", err)
    }
    return true, nil
}
```

`VerifySET`은 Rekor의 SET(Signed Entry Timestamp — Rekor 서버가 "이 항목이 이 시각에 로그의 이 인덱스에 존재한다"에 서명한 것)를 Rekor의 공개키로 검증한다. 요약하면 `cosign verify`가 실제로 확인하는 것 3가지: **(1) 서명 자체의 암호학적 유효성, (2) 인증서 체인이 Fulcio 루트까지 닿는지 + CT 로그 포함 증명(SCT), (3) 서명 항목이 Rekor 로그에 포함됐다는 증명(SET)**.

### 3-3. Public transparency log vs 비공개 IP — 긴장 관계

Rekor는 **설계상 공개**다. README L98의 경고 문구가 이를 최상위 UX 경고로 명시한다. keyless 흐름에서는 서명자의 OIDC 신원(이메일 등, Fulcio 인증서 subject), 서명 대상의 다이제스트, 서명 시각과 서명 값 자체가 전부 Rekor에 영구 공개된다. AIOS의 전략 패키지는 마켓플레이스에서 판매되는 **상업적 비공개 IP**다. `enterprise.py` L28의 `classification: Literal["public", "internal", "confidential", "restricted"]`이 이미 이 구분을 계약 레벨에서 강제하고 있고, `paper_strategy_projection.py` L45의 기본값도 `classification: ... = "confidential"`이다. 공개 Rekor에 "누가 언제 어떤 전략 해시에 서명했는가"가 영구 기록되는 것 자체는 전략의 **내용**을 누설하지는 않지만(다이제스트만 공개되므로), 세 가지가 문제가 된다: (1) **신원 상관관계 공격** — 같은 서명자가 짧은 기간에 여러 전략 해시에 서명한 이력이 공개 로그에서 그대로 노출돼 경쟁사가 출시 타임라인을 재구성할 수 있음, (2) **비가역성** — 한 번 Rekor에 오르면 삭제 불가하여 특정 전략을 나중에 완전히 폐기해도 서명 이력은 영구 공개로 남음, (3) keyless의 신원 모델(OIDC 이메일)이 "이 사람이 이 조직 소속으로 서명했다"는 기업 신원 증명에는 과하게 개인화돼 있음 — AIOS가 원하는 건 "AIOS 플랫폼이 서명했다"는 조직 신원이지 특정 직원의 이메일 노출이 아니다.

**결론**: keyless+공개 Rekor 조합은 AIOS 요구사항과 근본적으로 어긋난다. 대안은 두 가지이며 둘 다 cosign이 실제로 지원한다(4절에서 코드 근거 제시): (a) **private Rekor 인스턴스**를 운영하고 `--rekor-url`을 자체 서버로 돌리는 것, (b) **키 기반 서명 + `--tlog-upload=false`**로 아예 transparency log 자체를 스킵하고 AIOS 내부 Event Ledger를 감사 로그로 쓰는 것.

**AIOS 시사점**: Rekor의 "공개·비가역·개인 신원 결합"이라는 설계 결정은 오픈소스 공급망(누구나 검증 가능해야 함)에는 정확히 맞지만 AIOS의 "판매되는 비공개 IP + 플랫폼 조직 신원" 요구와는 정면으로 충돌한다. Fulcio/공개 Rekor를 그대로 쓰는 것은 기각하고, private Rekor 또는 키 기반+tlog 생략 중 하나를 4절에서 더 따진다.

---

## 4. 실전 도입 경로 — cosign을 라이브러리/CLI로 쓸 것인가, 자체 서명 체계로 갈 것인가

### 4-1. cosign은 키 기반(비-keyless) 서명도 1급 지원한다

`cmd/cosign/cli/sign.go`의 명령 예시(L83)에 이미 답이 있다: `cosign sign --key cosign.key --tlog-upload=false <IMAGE DIGEST>`. `cmd/cosign/cli/options/sign.go` L125가 플래그를 정의한다: `TlogUpload bool` 기본값 `true`지만 `false`로 끌 수 있다(단, 최신 cosign은 이 플래그를 "`--signing-config` 파일에서 transparency log 서비스를 아예 안 적는 방식"으로 deprecate 전환 중 — L127 `MarkDeprecated`). 즉 **cosign 자체가 "키 기반 서명 + transparency log 없음" 모드를 공식 지원**한다. README L257, L279, L447, L541 등 다수의 예시가 `cosign sign --key cosign.key ...`로 정적 키페어 서명을 보여준다(Fulcio/OIDC 관여 없음). 키는 PEM으로 저장되며 헤더는 `ENCRYPTED SIGSTORE PRIVATE KEY`(README L479-484)다. 또한 README L143-182 "Verify a container in an air-gapped environment"는 TUF 루트를 미리 pull해두고 오프라인에서 `cosign verify --key cosign.pub --offline --local-image ...`로 검증하는 흐름을 보여준다 — 이는 AIOS가 "플랫폼이 보유한 키로 서명하고, 프로모션/구매 시점에 오프라인/사설 환경에서 검증"하는 시나리오와 구조적으로 거의 동일하다.

### 4-2. cosign을 Python에서 쓸 수 있는가

cosign 자체는 Go 바이너리(`go.mod`, `cmd/cosign`)이며 Python 바인딩은 이 리포에 없다. 다만 Sigstore 생태계에는 별도 프로젝트 **`sigstore-python`**(PyPI 패키지명 `sigstore`, Sigstore 프로젝트가 직접 유지)이 존재한다 — "A tool for signing and verifying Python package distributions", "Support for keyless signature generation and verification with Sigstore", "A comprehensive CLI and corresponding importable Python API"(PyPI 설명 확인, 이번 조사에서 직접 클론하지 않음). Python 3.10+ 요구. 이건 **cosign의 Python 클라이언트가 아니라 sigstore-go와 별개로 구현된 독립 Python 구현체**이며, AIOS가 실제로 도입하려면 이 프로젝트를 별도로 검증해야 한다(이번 조사 범위 밖).

### 4-3. 실전 도입 경로 3가지 비교

| 경로 | 구현 난이도 | AIOS 적합성 |
|---|---|---|
| **(A) cosign CLI 서브프로세스 호출** — Go 바이너리를 Python 백엔드에서 `subprocess`로 실행, `--key`+자체 KMS 키+`--tlog-upload=false` 또는 사설 Rekor | 중간 — 바이너리 배포/버전 고정, Python↔Go 프로세스 경계 관리 필요 | 가능하지만 AIOS가 이미 Python 단일 스택인데 Go 바이너리 의존성 추가 비용 발생 |
| **(B) sigstore-python 라이브러리 직접 import** | 검증 필요(범위 밖) — keyless 중심 설계라 키 기반 모드 성숙도 확인 필요 | 스택 정합성은 (A)보다 좋으나 keyless 중심 철학이 AIOS 요구와 맞는지 별도 검증 필요 |
| **(C) AIOS 자체 해시 체인 + Ed25519/RSA 서명 컬럼, Statement/Predicate 데이터 모델만 차용** | 낮음 — `foundation/validation/domain/rules.py`의 `compute_result_hash`류 함수 옆에 서명 함수 추가, `key_ring.py` 옆에 비대칭 서명용 `SigningKeyRing` 신설 | AIOS 기존 패턴(순수 함수, dataclass, `_StrictContract` pydantic)과 가장 자연스럽게 통합. 서명 검증·키 회전·감사 가능성을 전부 자체 구현해야 하는 부담은 남는다 |

핵심 관찰: AIOS의 `KeyRing`(`core/security/key_ring.py`)은 **대칭키(AES-256, 32바이트) 전용**이다(L18 `_KEY_BYTES = 32`, 용도는 자격증명 암호화). 서명에 필요한 것은 비대칭키(Ed25519 또는 ECDSA)이므로, (C) 경로를 택하더라도 `KeyRing`을 그대로 재사용할 수 없고 **별도의 서명 키 관리 컴포넌트**(kid 체계는 `KeyRing`과 동일한 패턴을 복제 가능)를 새로 만들어야 한다 — 이건 (A)/(B)/(C) 어느 쪽을 쓰든 공통으로 필요한 신규 작업이다.

**AIOS 시사점**: cosign이 키 기반+tlog 생략 모드를 1급으로 지원한다는 사실(4-1)은 "Sigstore 도구 = 반드시 공개 keyless"라는 전제를 깨뜨린다 — 도구 자체는 AIOS 요구에 맞출 수 있다. 그러나 Go 바이너리 의존성(A) 또는 검증되지 않은 Python 라이브러리(B)를 들이는 비용은, AIOS가 이미 sha256/canonical-JSON 해시 체인과 pydantic 계약 패턴을 갖추고 있다는 점(C의 낮은 구현 난이도)과 비교하면 초기 ROI가 낮다. **가장 현실적인 경로는 (C)** — Statement/ResourceDescriptor의 데이터 모델(`_type`/`subject`/`predicateType`/`predicate` 봉투)만 그대로 베끼고, Envelope의 서명·검증은 자체 Ed25519 구현으로 대체하는 것이다. 이렇게 하면 나중에 외부 파트너/규제기관이 "SLSA/in-toto 호환이냐"고 물었을 때 데이터 포맷 레벨에서는 "예"라고 답할 수 있으면서, 서명 인프라는 AIOS가 완전히 통제한다.

---

## 5. 라이선스 및 성숙도

| 프로젝트 | 라이선스(확인) | 거버넌스 |
|---|---|---|
| sigstore/cosign | Apache-2.0 (`cosign/LICENSE` — "Apache License, Version 2.0") | CNCF (Sigstore는 CNCF Incubating) |
| in-toto/attestation | Apache-2.0 (`in-toto-attestation/LICENSE` — "Copyright 2021 in-toto Developers") | **CNCF 소속** — README.md L48-49: "The in-toto Attestation Framework is part of the [in-toto] project under the [CNCF]." (in-toto 프로젝트는 2023년 CNCF Graduated) |
| slsa-framework/slsa | **주의: Apache-2.0이 아니다** — `slsa/LICENSE.md`는 "Community Specification License 1.0"("It is not intended for source code" 명시) | OpenSSF 산하 (CNCF가 아님) |
| in-toto/in-toto (Python) | Apache-2.0 (`in-toto-python/LICENSE` — "Copyright 2018 New York University") | in-toto 프로젝트(CNCF)의 레퍼런스 구현 |

**정정 사항**: 사용자 요청에는 "Apache-2.0 for all three (confirm)"이라고 돼 있었으나, 확인 결과 **SLSA 스펙 리포는 Apache-2.0이 아니라 Community Specification License 1.0**이다(스펙 문서에 적용되는 라이선스이기 때문). cosign, in-toto-attestation, in-toto-python(Python 레퍼런스 구현) 3개 코드 리포는 전부 Apache-2.0이 맞다.

**Python 네이티브 구현 확인**: `github.com/in-toto/in-toto`가 정확히 이 역할이다. `pyproject.toml`(L8-11)에서 확인:

```toml
[project]
name = "in-toto"
description = "A framework to define and secure the integrity of software supply chains"
requires-python = ">=3.9"
```

Python 3.9~3.13 지원, PyPI 패키지명 `in-toto`(`pip install in-toto`), 실제 모듈 구조(`in_toto/`)는 `runlib.py`(링크 생성), `verifylib.py`(레이아웃 검증), `in_toto_sign.py`/`in_toto_verify.py`(CLI 엔트리), `models/metadata.py`(Metablock/DSSE 서명 객체) 등으로 구성돼 있다 — **AIOS가 Python 스택에서 직접 import해서 쓸 수 있는, Go 바이너리 의존성이 전혀 없는 완성된 구현체**다. 이는 cosign(Go 전용) 대비 결정적 이점이다: AIOS 백엔드가 이미 Python(FastAPI/pydantic) 스택이므로, in-toto Attestation의 Statement 생성·서명·검증을 이 라이브러리로 직접 처리할 수 있다면 4절의 (A)/(B) 경로보다 통합 비용이 낮다.

단, 주의할 점: `in-toto` PyPI 패키지는 in-toto의 **원래 레이아웃/링크 모델**(functionary가 정의된 step을 수행하고 그 결과에 서명하는 소프트웨어 공급망 무결성 모델, in-toto attestation Statement보다 역사적으로 앞선 개념)에 초점이 맞춰져 있다 — in-toto Attestation Framework(Statement/Predicate, 1절)의 최신 스펙을 얼마나 커버하는지는 `in_toto/models/metadata.py`를 더 깊이 읽어야 확정할 수 있다(이번 조사에서는 파일 존재만 확인, 내용까지는 미검증). AIOS가 실제 채택 전에는 이 패키지가 Statement v1 스펙을 얼마나 구현하는지 별도 확인이 필요하다.

**AIOS 시사점**: 세 프로젝트 모두 카피레프트가 아니라 Apache-2.0(SLSA는 스펙이므로 예외) — 라이선스는 도입 장벽이 아니다. 성숙도도 전부 CNCF/OpenSSF 산하로 충분하다. **Python 네이티브 `in-toto` 패키지의 존재는 "표준 데이터 모델만 차용"이 아니라 "실제 도구(적어도 in-toto 쪽)까지 도입"하는 옵션의 실행 비용을 낮춘다** — Go cosign 바이너리를 서브프로세스로 부르는 것보다 훨씬 자연스러운 통합 경로가 존재한다는 뜻이다. 다만 이 패키지가 Sigstore(Fulcio/Rekor)와는 무관하다는 점은 명확히 해야 한다 — in-toto Python 구현은 attestation의 **포맷과 로컬 서명/검증**을 다루지, Sigstore의 keyless PKI나 transparency log를 대체하지 않는다.

---

## 최종 결론

세 가지 선택지 — **(가) 표준 데이터 모델(Statement/Predicate)만 차용**, **(나) 실제 도구(cosign/Rekor)까지 도입**, **(다) 완전 자체 서명 체계** — 를 검토한 결과.

**(나)는 기각한다.** 근거는 3절이다 — cosign의 기본 흐름(keyless + 공개 Rekor)은 README L98의 "stored in public transparency logs and cannot be removed later"라는 경고가 그대로 말해주듯, 서명자 신원과 서명 이력을 영구 공개하는 것을 전제로 설계됐다. AIOS 전략 패키지는 판매되는 비공개 IP이고(`enterprise.py`의 `classification` 필드가 이미 `confidential`을 기본으로 강제), 이 요구와 공개 transparency log는 구조적으로 충돌한다. 사설 Rekor 인스턴스를 세우거나 `--tlog-upload=false`로 우회하는 것은 기술적으로 가능하지만(4-1), 그 순간 "Rekor를 쓴다"는 이점(범용 공개 검증 가능성, 생태계 호환)이 대부분 사라진다 — 사설 Rekor 유지보수 비용을 감수하면서 얻는 이득이 자체 감사 로그(Event Ledger)를 강화하는 것보다 크지 않다. cosign을 Go 바이너리로 들이는 통합 비용(4-3 (A))도 AIOS의 Python 단일 스택 원칙과 맞지 않는다.

**(다)만으로도 부족하다.** "자체 해시 체인에 서명 필드 하나 추가"는 구현이 가장 빠르지만, 5절이 보여주듯 Python 네이티브 `in-toto` 구현체가 이미 존재하고 라이선스 장벽도 없는 상황에서 봉투 포맷까지 새로 설계하는 것은 불필요한 재발명이다. 특히 향후 AIOS가 외부 감사·기관투자자·규제기관에 "우리 전략 아티팩트가 어떻게 검증됐는가"를 설명해야 할 때, `_type`/`subject`/`predicateType`/`predicate`라는 업계 표준 어휘로 답할 수 있는 것과 "AIOS 사내 포맷입니다"라고 답해야 하는 것의 신뢰도 차이는 크다.

**따라서 최종 권고는 (가)를 뼈대로, 서명 계층은 자체 구현하되 검증된 오픈소스 1차 구현(Python `in-toto`)의 유용한 부분을 선택적으로 재사용하는 혼합안이다**:

1. **데이터 모델**: in-toto Statement(`_type`, `subject[].digest`, `predicateType`, `predicate`)를 AIOS의 아티팩트 봉투 표준으로 채택한다. `StrategyPackage.artifact.content_hash`, `foundation/validation`의 `artifact_hash`/`result_hash`들을 전부 `subject[].digest`로 재표현할 수 있다(2절 표의 매핑).
2. **Predicate**: SLSA Provenance(`buildDefinition`/`runDetails`)는 "전략이 어떤 빌드 환경에서 만들어졌는가"(`build_environment_ref`) 부분에만 제한적으로 쓰고, "6개 체크가 `policy_hash`에 대해 어떤 판정을 냈는가"는 SLSA를 억지로 채우지 말고 **AIOS 전용 predicateType**(`aios.dev/predicates/validation-bundle/v1` 같은 것, in-toto의 "커스텀 predicate 등록" 관행을 그대로 따름)을 새로 정의한다 — in-toto 커뮤니티가 공식적으로 권장하는 확장 방식이다(`in-toto-attestation/README.md`의 "Want to propose a new predicate type?" 절).
3. **서명(Envelope)**: Sigstore keyless/공개 Rekor는 쓰지 않는다. 대신 (a) 플랫폼이 보유한 Ed25519 서명키로 DSSE Envelope을 만들거나 (b) `core/security/key_ring.py`와 동일한 kid 기반 패턴으로 신규 `SigningKeyRing`(비대칭키, PAPER/LIVE 분리 등 기존 관례 계승)을 만든다. Python `in-toto` 패키지(`in_toto/models/metadata.py`의 Metablock/서명 처리)를 먼저 평가해 재사용 가능하면 재사용하고, 안 맞으면 자체 구현한다 — 어느 쪽이든 **공개 transparency log는 배제**하고 AIOS의 Event Ledger를 "언제 누가 무엇에 서명/검증했는가"의 사설 append-only 기록으로 쓴다(목표 아키텍처 §3에서 "모든 위 화살표가 여기 기록"이라고 정의된 역할과 정확히 일치).
4. **검증 시점**: 마켓플레이스 프로모션/구매 시점에 AIOS 자체 검증 로직이 (1) Envelope 서명 유효성, (2) 서명 키가 AIOS 신뢰 루트(사설 CA 또는 플랫폼 키 목록)에 속하는지, (3) Event Ledger에 해당 서명 이벤트가 존재하는지(사설 "포함 증명")를 확인한다 — cosign의 3단계 검증(서명/인증서 체인/tlog 포함 증명, 3-2절)과 **구조적으로 동일하되 전부 사설 인프라 위에서** 수행한다.

요약하면: **포맷은 업계 표준(in-toto Statement)을 빌리고, PKI와 transparency log는 빌리지 않는다.** 그 경계선이 정확히 "공개 오픈소스 공급망 보안 도구"와 "비공개 상업 IP를 다루는 플랫폼"의 요구가 갈라지는 지점이기 때문이다.
