# 환경별 설정 프로필 (H-13, ADR-2026-09-09-B)

## 목적

지금까지는 `.env` 하나에 dev/staging/live 구분 없이 모든 설정이 섞여 있었다.
이 문서는 `AIOS_ENV`로 선택하는 3개 프로필(`config/dev.yaml`,
`config/staging.yaml`, `config/live.yaml`)과 그 로더
(`src/core/config/profile.py`)를 설명한다.

**비밀(자격증명·서명키 등)은 이 프로필에 넣지 않는다.** 계속 `.env` 또는
`KeyRing`(`src/core/security/key_ring.py`)에서 읽는다. 프로필 YAML에는
`log_level`, `cors_allowed_origins` 등 비밀이 아닌 설정만 둔다.

## 사용법

```python
from src.core.config.profile import load_profile_config

# AIOS_ENV 환경변수로 프로필을 고른다(기본값 "dev").
cfg = load_profile_config()
cfg.name                    # "dev" | "staging" | "live"
cfg.log_level                # "DEBUG" | "INFO" | "WARNING"
cfg.cors_allowed_origins     # tuple[str, ...]
cfg.extra                    # 스키마가 알지 못하는 나머지 키 — 자유 확장용
```

```bash
AIOS_ENV=staging python -m src.tools.some_cli   # config/staging.yaml을 읽는다
```

`AIOS_ENV`를 설정하지 않으면 `dev`가 기본값이다(가장 안전한 쪽으로
fail-closed). `dev`/`staging`/`live` 외의 값은 즉시
`InvalidProfileNameError`로 거부된다 — 오타로 존재하지 않는 프로필을 조용히
넘어가지 않는다.

## live 프로필의 paper/demo 혼입 가드 (핵심 DoD)

`AIOS_ENV=live`로 부팅할 때, 아래 세 환경변수 중 하나라도 "여전히
paper/demo"로 읽히면 `load_profile_config()`가
`LiveProfileContaminationError`를 던지고 **기동 자체를 거부**한다:

| 환경변수 | 거래소 |
|---|---|
| `KIS_PAPER_TRADING` | 한국투자증권 |
| `NH_PAPER_TRADING` | NH투자증권(나무) |
| `BITGET_PAPER_TRADING` | Bitget (`paptrading: 1` 데모 헤더에 대응하는 신규 도입 플래그 — 기존 `BITGET_DEMO_API_KEY`류 자격증명 변수와는 별개다) |

**fail-closed 판정 규칙**: 값이 `0`/`false`/`no`/`off`(대소문자 무관)일 때만
"명시적으로 껐다"고 인정한다. 그 외의 모든 경우 — 값이 아예 설정되지 않은
경우, `true`/`1`/`yes`/`on`인 경우, 심지어 오타처럼 알아볼 수 없는 값인
경우까지 — 전부 "아직 paper"로 간주해 live 부팅을 막는다. 즉 live로 넘어가는
쪽에서 실수로 플래그 하나를 빼먹으면(설정 자체를 안 하면) 자동으로 열리는
것이 아니라 자동으로 막힌다.

```bash
# live 부팅에 필요한 최소 조건 — 셋 다 명시적으로 꺼야 한다.
export AIOS_ENV=live
export KIS_PAPER_TRADING=false
export NH_PAPER_TRADING=false
export BITGET_PAPER_TRADING=false
```

이 가드는 프로필 로딩 단계의 검증일 뿐, 실제 실계좌 어댑터 생성을 여는
스위치가 아니다. 실계좌 어댑터(`demo_mode=False`) 생성 자체는 별도로
`AIOS_ALLOW_LIVE_ADAPTER=1`이 있어야 하는 독립된 3중 방어선
(`src/exchanges/factory.py`, ADR-2026-08-29-E, L4-13)이 이미 지키고 있다.
이 프로필 가드는 그 앞 단계 — "live 프로필을 골랐는데 설정이 아직 paper를
가리키고 있다"는 **구성 실수**를 잡는다.

## 프로필 파일 스키마

```yaml
environment: dev            # 필수. 파일명과 반드시 일치(dev.yaml -> "dev")
log_level: DEBUG            # 필수
cors_allowed_origins:       # 선택, 기본 []
  - http://localhost:5173
```

`environment` 필드가 파일명과 다르면(예: `staging.yaml` 안에
`environment: dev`) `ProfileSchemaError`로 거부된다 — 파일을 잘못
복사·붙여넣기한 사고를 로드 시점에 잡기 위해서다.

## 새 프로필 추가

새 환경(예: `canary`)이 필요하면:

1. `src/core/config/profile.py`의 `VALID_PROFILES`에 이름을 추가한다.
2. `config/canary.yaml`을 추가한다(`environment: canary` 필수).
3. live와 동급의 실거래 위험이 있는 프로필이라면 `assert_live_profile_safe`의
   `if profile_name != "live":` 분기를 그 프로필도 포함하도록 넓히는 것을
   ADR로 먼저 기록한다 — 임의로 조용히 추가하지 않는다.
