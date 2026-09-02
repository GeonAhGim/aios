# 11. 구현 규칙 보강 (재검토 2차 — 통화·정밀도·코드품질·환경) — v1.2

> **v1.2(2026-08-10) = "모든 문서 실제 구현가능성 검증" 라운드.** ①§11.6
> pyproject.toml이 lint/mypy 설정만 있고 실제 [project.dependencies](fastapi,
> sqlalchemy 등)가 없어 `pip install -e .`가 아무것도 설치 안 했을 상태 —
> 실제 의존성 목록 추가. ②§11.7 docker-compose.dev.yml이 POSTGRES_USER/
> PASSWORD/포트 매핑 없이 "개요"만 있었고, DB 이름도 07번 `.env.example`의
> `aios_dev`와 다른 `mihwa_dev`였음 — 완전한 실행가능 버전으로 완성, 이름 통일.

> **v1.1(2026-08-10) = "0번부터 재검토" 라운드 — 번호 충돌 정정.** §11.1~11.8이
> 정책문서(docx) 11장(의사결정 기록, 11.4 회귀금지목록 등)과 번호가 겹치는
> 것을 발견 — 06/07/08/09번과 동일 라운드에 동일 조치.

> 09번(자체 레드팀 재검토)에 이은 2차 검토. 09번이 "각 모듈 내부 로직"의 결함을 찾았다면,
> 본 문서는 "여러 모듈을 가로지르는 공통 규칙"의 공백을 다룬다 — 특히 다중 자산군(crypto+KRW)
> 확장으로 인해 처음 드러난 문제들이다.

## §11.1 통화(Currency) 처리 원칙 (v3.1 신설 — 최우선 반영)

**문제**: Bitget은 USDT 기준, KIS는 KRW 기준으로 잔고·손익을 반환한다. `AccountBalance`, `Position`, 8.2-C 포트폴리오 집계, Risk Engine의 자산 집중도(8.2-B)는 모두 "전체 자본 대비 비율"을 계산하는데, 통화 변환 없이 두 값을 더하면 계산 자체가 틀린다.

```python
# src/data/models/base.py 추가
class Currency(str, Enum):
    USDT = "USDT"
    KRW = "KRW"


class Money(BaseModel):
    """v3.1 신설 — 모든 금액 필드는 원시 Decimal이 아니라 이 타입을 쓴다.
    통화 단위 없는 Decimal 금액은 이제부터 코드 리뷰에서 반려 대상이다."""
    amount: Decimal
    currency: Currency

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)
        return Money(amount=self.amount + other.amount, currency=self.currency)


class FXRate(BaseModel):
    """환율 스냅샷. 8.1-A 다중소스 교차검증과 동일한 원칙 적용 대상 —
    단일 환율 소스에만 의존하지 않는다(Draft: 2개 이상 소스 평균)."""
    base: Currency
    quote: Currency
    rate: Decimal
    timestamp: datetime
    source: str
```

- **01_data_models.md 갱신 필요**: `AccountBalance.total`, `AccountBalance.available` 등 금액 필드를 `Decimal` 단독이 아니라 `Money`로 교체(실제 착수 시 반영).
- **포트폴리오 집계(8.2-C) 원칙**: 서로 다른 통화의 자산을 합산해야 하는 모든 지점(전체 자본 대비 비율 계산 등)은 반드시 하나의 기준 통화(Draft: USD)로 환산 후 계산한다. 환율 데이터가 없거나 오래된 경우(Draft: 5분 이상 경과) 해당 계산은 8.1-A와 동일하게 Data Distrust 취급 — 신뢰할 수 없는 환율로 계산된 Risk 판단은 하지 않는다.
- FX 데이터 소스는 실제 착수 시 확정(예: 거래소 자체 원화마켓 시세, 또는 외부 환율 API) — 지금은 원칙만 고정.

## §11.2 자산별 정밀도(Precision) 규칙

```python
# config/precision_policy.yaml (Draft)
precision:
  crypto:
    default_decimals: 8
  kr_equity:
    default_decimals: 0   # 원화 주식은 정수 단위(원 미만 없음)
```

- `Decimal` 필드에 값을 넣기 전, 자산군(`asset_class`)에 따라 반올림 규칙을 적용한다 — Bitget 응답을 그대로 저장하지 않고 `quantize()`로 명시적 반올림.
- 반올림 방식은 `ROUND_DOWN`(자산 손실 방향 보수적 처리) 기본값 — 수량 절상으로 인한 과다 주문 방지.

## §11.3 커스텀 예외 계층 (v3.1 신설)

```python
# src/core/exceptions.py
class MihwaError(Exception):
    """모든 프로젝트 커스텀 예외의 루트."""

class CurrencyMismatchError(MihwaError):
    def __init__(self, c1: "Currency", c2: "Currency"):
        super().__init__(f"통화 불일치: {c1} vs {c2}")

class ExchangeAPIError(MihwaError):
    """거래소 API 호출 실패 공통 부모. 재시도 가능 여부를 서브클래스로 구분."""

class RetryableExchangeError(ExchangeAPIError): ...
class FatalExchangeError(ExchangeAPIError): ...  # 인증 실패 등 재시도 무의미

class ZoneViolationError(MihwaError):
    """15.6-A FROZEN Zone 경로를 SCAFFOLD 코드가 잘못 import하려 할 때(런타임 방어선)."""

class EventHandlerError(MihwaError):  # 05번에서 이미 정의 — 루트를 MihwaError로 통일
    ...
```

- 모든 프로젝트 커스텀 예외는 `MihwaError`를 상속한다 — `except Exception`으로 뭉뚱그리지 않고 `except MihwaError`로 "우리 코드가 예상한 실패"와 "예상 못한 버그"를 구분 가능하게 한다.

## §11.4 시간 동기화 원칙

- 모든 서버는 NTP 동기화를 전제한다(배포 환경에서 기본 활성화 확인 — 클라우드 VM은 보통 기본 제공).
- `Order.created_at` 등 타임스탬프는 로컬 시계가 아니라, 가능한 경우 거래소 응답의 서버 타임스탬프를 우선 신뢰한다 — 로컬 시계 드리프트가 8.2-D 지연 벤치마크 측정치를 왜곡할 수 있다.
- Rate Limit 윈도우 계산(예: "초당 N회") 등 정확한 시간 기준이 필요한 로직은 이 원칙을 명시적으로 재확인한다.

## §11.5 데이터 보존 정책 (Draft)

| 테이블 | 보존 기간 | 근거 |
|---|---|---|
| `audit_log` | 무기한 (WORM) | 8.10, 16.3 — 감사 목적상 삭제 자체가 원칙 위반 |
| `orders`, `positions` | 무기한 | 회계·세무 근거자료 |
| `memory_entries` | 무기한 (단, 4.6-A 롤백 이력과 함께) | Memory 오염 사후 조사에 과거 이력 필요 |
| Tick 단위 시세(`market_data` 등 별도 테이블 예정) | Draft: 90일 이후 압축/아카이브 | 무한 누적 시 DB 용량 문제 — 구체 정책은 Phase 2 백테스트 시스템 착수 시 재확정 |

## §11.6 코드 품질 도구 (Draft)

```toml
# pyproject.toml — "모든 문서 실제 구현가능성 검증" 라운드에서 발견: lint/mypy
# 설정만 있고 실제 [project] 의존성 목록(fastapi, sqlalchemy 등)이 어디에도
# 없어 `pip install -e .`(§16.12-A)를 실행해도 아무것도 설치되지 않았을 상태.
[project]
name = "mihwa-aios"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.13",
    "pyjwt>=2.9",
    "argon2-cffi>=23.1",          # FD-11.1 비밀번호 해시
    "cryptography>=43.0",          # FD-12.1/11.5 AES-256-GCM
    "httpx>=0.27",                 # 05번 §5.4, Exchange Adapter REST
    "websockets>=13.0",            # 05번 §5.4, WebSocket 시세
    "TA-Lib>=0.4.32",              # FD-14.1
    "python-dotenv>=1.0",
]

[tool.ruff]
line-length = 100
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
strict = true
disallow_untyped_defs = true
# FROZEN Zone 인터페이스 파일은 strict 예외 없음 — 오히려 더 엄격히 적용
```

- Lint/타입체크 실패 시 CI 자동 거부(08번 §8.7 CI 파이프라인에 단계 추가).
- Docstring 스타일: Google Style로 통일(이 문서 시리즈의 기존 예시 코드와 일관되게).
- 함수 최대 길이 가이드(Draft): 50줄 초과 시 분리 검토 — 10번 문서의 "최소단위 커밋" 철학과 일치.
- `TA-Lib` 파이썬 패키지는 시스템 레벨 C 라이브러리 선설치가 필요(Draft — OS별
  설치법은 착수 시 README에 명시, `brew install ta-lib` 또는 apt 패키지 등).

## §11.7 로컬 개발 환경 (Draft 개요)

```yaml
# docker-compose.dev.yml — "모든 문서 실제 구현가능성 검증" 라운드에서 완성
# (POSTGRES_USER/PASSWORD/포트 누락 및 §7.3 DATABASE_URL과 DB 이름 불일치
# 발견 — 舊 "mihwa_dev" vs .env.example의 "aios_dev", 이 파일 기준으로 통일)
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: aios_dev
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:
  # Event Bus는 Phase 1 In-process이므로 별도 컨테이너 불필요(05번 원칙)
```

```bash
docker compose -f docker-compose.dev.yml up -d
# → 07번 §7.3 .env.example의 DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/aios_dev
#   와 정확히 일치(§16.12-A 실행 순서의 alembic upgrade head 이전 단계)
```

- Phase 1은 PostgreSQL 컨테이너 1개만 있으면 로컬 개발 가능 — Event Bus가 in-process라 추가 인프라(Redis 등) 불필요, 이 자체가 05번 "과잉설계 방지" 결정의 실익이다.
- `.env.example`(07번 §7.3)을 복사해 `.env` 생성 후 API 키만 채우면 즉시 개발 가능한 것을 목표로 한다.

## §11.8 아직 의도적으로 미확정인 것 (09번 §9.2 원칙 재적용)

- FX 데이터 소스 특정 업체 — 실제 통화 처리 필요 시점(Phase 1 후반 또는 Phase 4)에 재검토.
- Tick 데이터 아카이브 방식(S3? 압축 테이블?) — Phase 2 Backtest System 설계 시 함께 결정하는 것이 합리적.
- Docker 프로덕션 배포 파일 — 로컬 개발용 compose와 실제 배포 인프라는 팀이 인프라 제공자(AWS/GCP 등)를 정한 후 작성.
