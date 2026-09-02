# 16. FD-11~21 백엔드 시그니처 (FastAPI + Pydantic + SQLAlchemy async) — v1.10

> 근거: 기능설계문서_v1.4(FD-11~21), 15번 API 스펙, 04번/13번 DB스키마,
> ADR-2026-08-10-B(기술스택 확정)
> **v1.0(2026-08-10)**: 최초 작성 — FD-11~21 전체 라우터·Pydantic 모델·Service
> 클래스 시그니처.
> **v1.1(2026-08-10)**: 재점검 라운드 반영 — CamelModel 직렬화 컨벤션 신설,
> 누락 엔드포인트 6개 추가(`/auth/logout`, 거래소 잔고/포지션 조회, 마켓플레이스
> 상세/제출/내구매목록), 경로명 정정(`/signup`→`/register`), `paused_by` 필드
> 추가, 페이지네이션 적용(마켓플레이스·알림·운영자 목록), 금전 관련 POST에
> Idempotency-Key 적용.
> **v1.2(2026-08-10)**: 전체 타입 참조 전수 스캔(정의 vs 참조 대조) — 참조만
> 되고 정의가 없던 서비스 클래스 3개(`RiskPolicyGate`, `ApprovalService`,
> `SafetyLayerStatusProvider`) + 응답/결과 타입 5개(`ApprovalRequest`,
> `UserResponse`, `PurchaseResult`, `PurchasedStrategyAccess`(舊 미정의
> `StrategyInstance` 대체), `AccountService`) 추가. §16.0에 "DB 테이블명과
> 1:1 대응하는 대문자 타입은 SQLAlchemy ORM 엔티티" 컨벤션 명시.
> **v1.3(2026-08-10)**: 리뷰/분쟁/검색정렬 라우터 추가(FD-13.8~13.10), 출금권한
> 검증(FD-12.1), 결제확인(FD-18.5a/b), FD-9.4b 재가동승인 대응.
> **v1.4(2026-08-10) = "0번부터 재검토" 라운드 — 번호 충돌 정정.** 이 문서의
> 최상위 섹션(舊 "## 16.2", "## 16.3" 등)이 **정책문서(docx) 16장**(DevEngine
> 아키텍처 불변성, 하위 조항 16.2 Capability Token/16.3 불변감사로그/16.6
> 메타통제면 등)과 번호가 정확히 겹치는 것을 발견 — "FD-9.2/9.6/16.2" 같은
> 축약 표기는 실제로 FD-16.2를 가리켰는데 "FD-" 접두어가 빠져 정책문서
> 16.2로 오독될 위험이 있었다. **조치**: 이 문서의 모든 최상위 섹션 헤더를
> "## 16.X" → "## §16.X"로 전면 변경 — 이 문서 내에서 **"§16.X"는 항상 이
> 문서 자신의 섹션, 접두어 없는 "16.X"는 정책문서(docx) 16장의 조항**이라는
> 표기 컨벤션을 확정한다(기능설계문서 v1.16에서도 동일 라운드에 상응 정정).
> **v1.5(2026-08-10) = "모든 문서 실제 구현가능성 검증" 라운드(1차).** 서버
> 기동 명령이 어디에도 없어 §16.12-A 신설(pip install→alembic→uvicorn 전체
> 시퀀스).
> **v1.6(2026-08-10) = 동일 라운드(2차).** `src/db/`(SQLAlchemy 세션+ORM
> 모델) 계층 전체 신설 — §16.0이 계속 import해온 `get_db_session()`의 실제
> 구현체가 어디에도 없었음. 신설 과정에서 2차 발견: `session.py`가 존재하지
> 않는 `get_settings()`를 참조하고 있었음 — 03번의 실제 API인
> `Loader().load_env_secrets() -> SecretBundle`로 정정(SecretBundle 자체도
> 01번에 신규 정의, v1.3).
> **v1.7(2026-08-10)**: §16.12-A 실행순서 최상단에 `docker compose up -d`
> 단계 추가 — 11번 §11.7이 그동안 "개요"만 있어 DB 자체가 없는 상태에서
> alembic을 실행하려 했던 순서 공백을 발견해 정정.
> **v1.8(2026-08-10) = "클로드 코드 구현가능성" 검증 라운드.**
> `SafetyLayerStatusProvider.is_blocked()`가 실제로 무엇을 조회하는지
> 불명확했음(정의는 있었으나 "무엇을 쿼리하는지" 없음) — 개별 실행 차단은
> `strategy_executions.paused_by`(신규 테이블 불필요), 시스템 전역 차단은
> `system_safety_state.circuit_breaker_level`(04번 v1.5 신규)로 확정.
> **v1.10(2026-08-28) = 다자산군(Multi-Asset-Class) 확장 라운드.** ADR-2026-08-28
> 반영 — `StrategyCreateRequest`(§16.4)에 파생상품 Optional 필드 추가
> (asset_class/option_type/strike_price/expiry_date/underlying_symbol),
> 마켓플레이스 검색(§16.3 `search_listings`)의 `asset_class` 파라미터 타입을
> `AssetClass | None`로 명시, `ExchangeCredentialService`에
> `get_capabilities_for_user()` 추가(사용자가 등록한 거래소가 실제 지원하는
> 자산군 목록을 프론트가 조회할 수 있어야 파생상품 UI를 조건부로 노출 가능).
>
> **v1.9(2026-08-10) = "구현자 리뷰 대조" 라운드.** §16.0-B 신설 — FD-9의
> 5개 백그라운드 루프(Watchdog·Split-Brain·Circuit Breaker·Data Distrust·
> Reconciliation)에 예외 발생 시 태스크가 조용히 죽는 것을 막는 공통 방어
> 코드(`run_safety_loop`)가 어디에도 없었음(안전장치가 예외 하나로 영구
> 정지될 수 있던 상태 — 발견 중 가장 심각한 축). `src/api/main.py`에
> `lifespan`으로 EventBus 시작/종료 연결(05번 v1.2 싱글톤과 매칭).
> 상태: 전체 SCAFFOLD-READY — FROZEN 판단 로직(FD-8)을 직접 호출하지 않고,
> `src/core/strategy|portfolio|risk|executor/`의 FROZEN 인터페이스를 통해서만
> 접근한다(03번 §3.9 Zone 경계 그대로 준수).
> 패턴: 라우터(`APIRouter`)는 요청 검증·응답 직렬화만 담당하고 실제 로직은
> Service 클래스에 위임한다(01/02/03번의 "판단은 각자 계층의 책임" 원칙과 동일
> 정신 — 라우터가 비즈니스 로직을 갖지 않는다).

## §16.0-B `src/core/safety/` 백그라운드 루프 공통 방어 패턴 (신규 — "구현자
리뷰 대조" 라운드에서 발견: FD-9의 5개 루프(Watchdog·Split-Brain·Circuit
Breaker·Data Distrust·Reconciliation)가 전부 "5초마다"/"1분마다" 주기적
실행을 서술하면서도, **루프 내부 예외 발생 시 태스크 자체가 조용히
죽는 것을 막는 공통 방어 코드가 어디에도 없었음** — 안전장치가 예외
하나로 영구히 멈춰버리는 것이 이 프로젝트가 막으려는 바로 그 실패
시나리오와 정확히 같은 종류의 위험이라 가장 심각한 발견 중 하나.)

```python
# src/core/safety/base_loop.py
# STATUS: SCAFFOLD-READY
import asyncio
import logging

logger = logging.getLogger("aios.safety")


async def run_safety_loop(name: str, interval_sec: float, tick_fn) -> None:
    """FD-9.1~9.6 5개 루프 전부가 이 함수로 감싸서 실행된다 — 개별 루프가
    각자 try/except를 반복 작성하지 않도록 공통화. `tick_fn`은 매 주기
    1회 실행되는 순수 로직(예: WatchdogService.check_once).

    핵심 원칙: 예외가 발생해도 루프 자체는 절대 종료되지 않는다 — CRITICAL
    로그(정책문서 8.10 audit_log 대상) 남기고 다음 주기에 재시도. 안전장치가
    예외 하나로 영구히 멈추는 것은 안전장치가 아예 없는 것보다 위험하다
    (거짓 안전감을 준다는 점에서 오히려 더 나쁨)."""
    while True:
        try:
            await tick_fn()
        except Exception as e:
            logger.critical(f"[{name}] 안전장치 루프 예외 — 계속 재시도: {e}", exc_info=True)
            # audit_log(FD-7.2) INSERT + audit.decision.logged 이벤트 발행 필요(착수 시 연결)
        await asyncio.sleep(interval_sec)
```

```python
# src/core/safety/watchdog.py 사용 예 (나머지 4개 루프도 동일 패턴)
# STATUS: SCAFFOLD-READY
from src.core.safety.base_loop import run_safety_loop

class WatchdogService:
    async def check_once(self) -> None:
        """FD-9.1/9.2 — 1회 관측+판정. 실제 로직은 이 문서 §16.6과 무관하게
        FD-9의 순수 판단 로직(WatchdogSnapshot/WatchdogDecision)을 그대로 구현."""
        ...

    async def run(self) -> None:
        """§16.12 lifespan에서 `asyncio.create_task(WatchdogService().run())`으로
        기동. FD-9.1 관측 주기(5초)를 그대로 사용."""
        await run_safety_loop("watchdog", interval_sec=5.0, tick_fn=self.check_once)
```

- 나머지 4개(`split_brain.py`, `circuit_breaker.py`, `data_distrust.py`,
  `reconciliation.py`)도 각자의 Service 클래스에 `check_once()`를 구현하고
  `run_safety_loop()`로 감싸는 동일 패턴을 따른다 — FD-9.3/9.4/9.5는
  5초, FD-9.6은 1분(FD-9.6 원문 Draft 주기) 간격.
- `src/api/main.py`(§16.12) `lifespan`에서 5개 루프 전부를
  `asyncio.create_task(...)`로 기동해야 한다(착수 시 §16.12 코드에 추가 필요
  — 지금은 EventBus 시작/종료만 있음).

## §16.0-A `src/db/` 계층 (신규 — "모든 문서 실제 구현가능성 검증" 라운드에서
발견: `from src.db.session import get_db_session`을 §16.0이 계속 import해왔지만,
`src/db/` 디렉토리 자체도 SQLAlchemy 선언형 ORM 모델 클래스도 어디에도
실제로 작성된 적이 없었음 — 04번은 순수 SQL DDL만 있었지, 그 DDL을 실행
가능한 파이썬 코드로 옮긴 적이 한 번도 없었다는 뜻)

```python
# src/db/base.py
# STATUS: SCAFFOLD-READY
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """모든 SQLAlchemy ORM 모델의 공통 부모. 04/13번 DDL과 1:1 대응하는
    모델 클래스들이 이 Base를 상속한다."""
    pass
```

```python
# src/db/session.py
# STATUS: SCAFFOLD-READY
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.core.loader.config_loader import Loader
# "모든 문서 실제 구현가능성 검증" 라운드 정정: 존재하지 않는 get_settings()
# 대신 03번 §3.1이 실제로 정의한 Loader().load_env_secrets() -> SecretBundle
# 사용(SecretBundle 자체도 01번에 정의가 없어 이번에 함께 신설, 01번 참조).

_secrets = Loader().load_env_secrets()
engine = create_async_engine(_secrets.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_db_session() -> AsyncSession:
    """§16.0 get_db()가 참조하는 실제 구현. FastAPI Depends 체인의 최하단."""
    async with AsyncSessionLocal() as session:
        yield session
```

```python
# src/db/models/users.py
# STATUS: SCAFFOLD-READY
# 대표 예시 1개만 상세 작성 — 나머지 테이블(strategy_executions, orders,
# notifications, disputes, reviews 등 04/13번 DDL 전체)은 동일 패턴으로
# 기계적 생성 가능(FD-4.2가 이미 확립한 "세분화 예시" 관례와 동일 정신,
# 17.9-A 과잉설계 방지 — 모든 테이블을 여기 일일이 받아쓰지 않는다).
from datetime import datetime
from uuid import UUID
import uuid
from sqlalchemy import String, Boolean, TIMESTAMP, func
from sqlalchemy.orm import Mapped, mapped_column
from src.db.base import Base

class User(Base):
    """13번 §13.2 CREATE TABLE users DDL과 1:1 대응."""
    __tablename__ = "users"

    user_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    risk_profile: Mapped[str | None] = mapped_column(String(20))
    risk_profile_assessed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    is_verifier: Mapped[bool] = mapped_column(Boolean, default=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    seller_suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # CHECK 제약(status, risk_profile 값 목록)은 Alembic 마이그레이션에서
    # server-side CHECK로 강제 — SQLAlchemy 모델 레벨에서는 문자열 타입만
    # 선언(값 검증은 FastAPI Pydantic 요청 모델이 이미 담당, 이중 검증 과잉설계
    # 방지 — 17.9-A).
```

- **나머지 모델 클래스 작성 원칙**: `src/db/models/{도메인}.py` 1파일 1테이블
  그룹(위 `users.py` 패턴 참조) — `strategy_executions.py`,
  `orders_positions.py`, `notifications.py`, `marketplace.py`(listings/
  purchases/reviews/disputes), `withdrawal_whitelist.py` 등. 04번/13번 DDL의
  컬럼·타입·제약을 그대로 SQLAlchemy `Mapped[...]` 문법으로 옮기는 기계적
  작업 — 착수 시 개발자가 직접 순회하며 생성.



```python
# src/api/schemas/base.py
# STATUS: SCAFFOLD-READY
# 재점검 라운드에서 발견: 16번(Python snake_case)과 17번 프론트엔드 문서
# (TypeScript camelCase)가 필드명 변환 규칙 없이 서로 다른 컨벤션을 가정하고
# 있었음 — 이대로 구현했으면 프론트가 API 응답을 파싱하지 못했을 것.
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """§16.1~16.11의 모든 요청/응답 Pydantic 모델은 BaseModel이 아니라 이
    CamelModel을 상속한다(이 문서 전체의 BaseModel 표기는 지면상 축약 —
    실제 구현 시 전부 CamelModel로 교체). populate_by_name=True로 파이썬
    코드 내부에서는 snake_case를 그대로 쓰면서, JSON 직렬화 시에만
    camelCase로 자동 변환된다."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
```

```python
# src/api/exceptions.py
# STATUS: SCAFFOLD-READY
# 재점검 라운드에서 발견: 11번 §11.3 MihwaError 계층(CurrencyMismatchError,
# ExchangeAPIError, ZoneViolationError, EventHandlerError)엔 FD-11~21의
# 비즈니스 규칙 위반(예: 탈퇴 시 RUNNING 실행 존재, 잘못된 상태 전이)에 맞는
# 서브클래스가 없어서 16번 초안이 순수 ValueError를 직접 썼었음 — 11번 원칙
# ("except MihwaError로 우리 코드가 예상한 실패를 구분") 위반이므로 신설.
from src.core.errors import MihwaError  # 11번 §11.3 루트


class BusinessRuleViolationError(MihwaError):
    """FD-11~21 전반의 비즈니스 규칙 위반 공통 부모(11번 MihwaError 서브클래스
    신설). 예: RUNNING_EXECUTION_EXISTS(FD-11.4), ADMIN_STATUS_TRANSITION_INVALID
    (FD-18.3). 라우터가 이 예외를 잡아 15번 §15.3 표준 에러 포맷으로 변환한다."""
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        super().__init__(message)
```


> **타입 표기 컨벤션(재점검 라운드에서 8개 스캔·정정 후 명시)**: 이 문서 전체에서
> `"StrategyExecution"`, `"StrategyListing"`, `"ExchangeCredential"`,
> `"RiskProfileHistory"`, `"Dispute"`, `"UserApprovalSettings"`처럼 04번/13번
> DB스키마의 테이블명과 1:1로 대응하는 대문자 타입은 **SQLAlchemy ORM 행
>객체**(`src/db/models/`, 10번 문서 §10.3)를 가리킨다 — Pydantic으로 다시
> 정의하지 않는다(테이블 정의가 이미 04번/13번에 있는데 여기서 또 선언하면
> 두 곳이 어긋날 위험만 커짐, 정책문서 17.9-A 과잉설계 방지 원칙과 동일 정신). Service
> 계층 내부(라우터에 닿기 전)에서만 쓰이고, 라우터가 반환하기 직전 항상 `*Response`
> Pydantic 모델(예: `ExecutionResponse`)로 변환된다.

```python
# src/api/deps.py
# STATUS: SCAFFOLD-READY
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.session import get_db_session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_db() -> AsyncSession:
    """모든 라우터가 공유하는 DB 세션 — FastAPI Depends로 주입."""
    async with get_db_session() as session:
        yield session


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> "User":
    """FD-11.1 — JWT 검증 후 현재 사용자 반환. 실패 시 401.
    FD-18(운영자 도구) 라우터는 이 위에 get_current_admin()을 추가로 겹쳐 쓴다."""
    ...


async def get_current_admin(user: "User" = Depends(get_current_user)) -> "User":
    """FD-18 — is_platform_admin=False면 403. 15.6 RBAC 그대로 구현."""
    if not user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "관리자 권한이 필요합니다.")
    return user


async def get_current_verifier(user: "User" = Depends(get_current_user)) -> "User":
    """FD-18.1 — is_verifier=False면 403."""
    ...


class PaginationParams(BaseModel):
    """15번 문서 §15.1 페이지네이션 컨벤션 — 재점검 라운드에서 발견: 이 컨벤션이
    이미 정의돼 있었는데 FD-14~21 신규 목록 API(마켓플레이스 리스팅, 알림 이력,
    실행 목록, 운영자 목록 등)에 하나도 적용이 안 돼 있었음. 아래 공통 의존성으로
    일괄 적용."""
    page: int = 1
    size: int = 20


async def pagination(page: int = 1, size: int = 20) -> PaginationParams:
    return PaginationParams(page=page, size=size)


class PageMeta(BaseModel):
    total: int
    page: int
    size: int


class PaginatedResponse(BaseModel):
    """목록 API 공통 응답 래퍼. `{"data": [...], "meta": {...}}` — 15번 문서
    §15.1과 동일. 이 문서의 개별 라우터가 `list[XxxResponse]`로 표기된 곳은
    지면 축약이며, 실제로는 `PaginatedResponse[XxxResponse]`(제네릭)로 감싼다."""
    data: list
    meta: PageMeta
```

---

## §16.1 FD-11 인증·계정 — `src/api/routers/auth.py`

```python
# STATUS: SCAFFOLD-READY
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)  # FD-11.1 Draft 강도 규칙


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None  # FD-11.1 처리단계3 — mfa_enabled 시 필수


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """재점검 라운드에서 발견: `GET /users/me` 등 여러 곳이 참조만 하고
    정의가 없었음."""
    user_id: "UUID"
    email: str
    mfa_enabled: bool
    risk_profile: str | None = None  # FD-15.2, 미평가 시 None(이론상 발생 안 함)
    status: str  # ACTIVE|SUSPENDED|PENDING_DELETION|DELETED
    is_verifier: bool = False       # FD-18
    is_platform_admin: bool = False  # FD-18
    created_at: "datetime"


class MfaSetupResponse(BaseModel):
    qr_code_url: str
    secret: str  # 클라이언트가 인증앱 수동 등록 시에만 사용, 응답 후 서버 보관은 암호화(FD-11.2)


class MfaVerifyRequest(BaseModel):
    totp_code: str


class ApprovalSettingsRequest(BaseModel):
    mode: str  # "SOLO" | "DUAL" — FD-11.3
    second_approver_contact: str | None = None


class WithdrawalRequest(BaseModel):
    password: str  # FD-11.4 재인증


@router.post("/register", status_code=201)
async def signup(body: SignupRequest, db=Depends(get_db)) -> TokenResponse:
    """FD-11.1 가입. 경로명은 15번 문서 §15.2 기존 명명(/auth/register) 그대로 유지
    (재점검 라운드에서 /signup으로 어긋나 있던 것을 발견해 정정).
    AuthService.signup() 위임."""
    ...


@router.post("/logout")
async def logout(user=Depends(get_current_user), db=Depends(get_db)) -> dict:
    """15번 문서 §15.2 — 세션 무효화. 재점검 라운드에서 16번 문서에 누락된 것을
    발견해 추가. AuthService.invalidate_token() 위임(Draft: JWT 블랙리스트 방식,
    stateless JWT라 서버측 무효화 메커니즘은 착수 시 확정 필요)."""
    ...


@router.post("/login")
async def login(body: LoginRequest, db=Depends(get_db)) -> TokenResponse:
    """FD-11.1 로그인. 실패 시 401 + 계정열거 방지 일반화 메시지(FD-11.1 예외상황)."""
    ...


@router.post("/mfa/setup")
async def setup_mfa(user=Depends(get_current_user), db=Depends(get_db)) -> MfaSetupResponse:
    """FD-11.2."""
    ...


@router.post("/mfa/verify")
async def verify_mfa(body: MfaVerifyRequest, user=Depends(get_current_user), db=Depends(get_db)) -> dict:
    """FD-11.2 — 검증 성공 시에만 mfa_enabled=True 확정."""
    ...


@router.get("/users/me")
async def get_me(user=Depends(get_current_user)) -> "UserResponse":
    ...


@router.put("/users/me/approval-settings")
async def update_approval_settings(
    body: ApprovalSettingsRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> ApprovalSettingsRequest:
    """FD-11.3 — mandatory_wait_seconds<60 시도는 DB CHECK 제약이 최종 방어선,
    여기서는 서비스 레벨에서도 동일 검증(이중 방어, FD-17.4와 동일 패턴)."""
    ...


@router.delete("/users/me", status_code=202)
async def request_deletion(
    body: WithdrawalRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> dict:
    """FD-11.4 — RUNNING 실행 존재 시 409. AccountService.request_deletion() 위임."""
    ...


class WhitelistEntryRequest(BaseModel):
    exchange: str
    destination_address: str
    label: str | None = None
    password: str  # 재인증
    totp_code: str  # MFA 재확인 — FD-11.4보다 높은 보안강도 요구


class WhitelistEntryResponse(BaseModel):
    id: int
    exchange: str
    destination_address: str
    label: str | None
    created_at: "datetime"


@router.post("/users/me/withdrawal-whitelist", status_code=201)
async def register_whitelist_entry(
    body: WhitelistEntryRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> WhitelistEntryResponse:
    """FD-11.5 — "다시 0번부터" 라운드 신설: FD-10.3(패닉 프롬프트)이 참조만
    하던 화이트리스트를 채우는 기능 자체가 없었음을 발견해 추가. 위기
    상황 중(FD-9 카운터파티 리스크 심각 신호 활성 상태) 등록 시도 시
    409(WHITELIST_REGISTRATION_BLOCKED_DURING_CRISIS) — 정책문서 20.1-B
    "위기 이전 준비" 원칙의 실제 강제 지점."""
    ...


@router.get("/users/me/withdrawal-whitelist")
async def list_whitelist_entries(
    user=Depends(get_current_user), db=Depends(get_db)
) -> list[WhitelistEntryResponse]:
    ...
```

```python
# src/services/auth_service.py
# STATUS: SCAFFOLD-READY
class AuthService:
    """FD-11 전담 — Argon2id 해시, JWT 발급, 계정 잠금 카운터를 캡슐화.
    라우터는 이 클래스 메서드만 호출하고 해시·토큰 로직을 직접 다루지 않는다."""

    async def signup(self, email: str, password: str) -> "User": ...

    async def authenticate(self, email: str, password: str, totp_code: str | None) -> "User":
        """FD-11.1 예외상황 3종(계정 미존재/5회 실패 잠금/SUSPENDED) 전부 여기서 처리,
        모두 동일한 일반화 메시지로 라우터에 401 전달 — 원인별 분기를 라우터에 노출 안 함."""
        ...

    def issue_token(self, user: "User") -> str: ...

    async def setup_mfa(self, user: "User") -> tuple[str, str]:  # (qr_url, secret)
        ...

    async def verify_and_enable_mfa(self, user: "User", totp_code: str) -> bool: ...


class AccountService:
    """FD-11.3/11.4 전담 — 승인설정 변경, 탈퇴 절차. 재점검 라운드에서
    라우터가 참조만 하고 정의가 없던 것을 발견해 추가."""

    async def update_approval_settings(
        self, user_id: "UUID", body: ApprovalSettingsRequest
    ) -> "UserApprovalSettings":
        """FD-11.3 — mandatory_wait_seconds<60 시도 시 서비스 레벨에서도 거부
        (DB CHECK 제약과 이중 방어)."""
        ...

    async def request_deletion(self, user_id: "UUID") -> "User":
        """FD-11.4 — RUNNING 실행(FD-16) 존재 여부 먼저 확인, 존재 시
        BusinessRuleViolationError("RUNNING_EXECUTION_EXISTS", ...) 발생
        (라우터가 409로 변환, 재점검 라운드에서 순수 ValueError → MihwaError
        계층으로 정정).
        없으면 status='PENDING_DELETION', deletion_requested_at 기록."""
        ...

    async def register_withdrawal_whitelist(
        self, user_id: "UUID", body: "WhitelistEntryRequest",
        safety_layer_status: "SafetyLayerStatusProvider",
    ) -> "WithdrawalWhitelistEntry":
        """FD-11.5 — safety_layer_status로 현재 카운터파티 리스크 심각
        신호 활성 여부 확인(FD-9 재사용) — 활성 상태면
        BusinessRuleViolationError("WHITELIST_REGISTRATION_BLOCKED_DURING_CRISIS")
        발생(라우터가 409로 변환). audit_log(FD-7.2) 기록 필수."""
        ...
```

---

## §16.2 FD-12 거래소 연동 — `src/api/routers/exchanges.py`

```python
# STATUS: SCAFFOLD-READY
from pydantic import BaseModel
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/exchange-credentials", tags=["exchanges"])


class CredentialRequest(BaseModel):
    exchange: str  # "bitget" | "kis"
    api_key: str
    api_secret: str
    app_key: str | None = None    # KIS 전용
    app_secret: str | None = None # KIS 전용


class CredentialResponse(BaseModel):
    id: int
    exchange: str
    is_active: bool
    registered_at: "datetime"


@router.post("", status_code=201)
async def register_credential(
    body: CredentialRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> CredentialResponse:
    """FD-12.1 — 등록 직후 FD-3.2 잔고조회 1회로 유효성 검증(무효 시 저장 자체를 안 함)."""
    ...


@router.delete("/{credential_id}")
async def revoke_credential(
    credential_id: int, user=Depends(get_current_user), db=Depends(get_db)
) -> dict:
    """FD-12.1 해지 — revoked_at 갱신, 물리 삭제 아님."""
    ...


@router.get("")
async def list_credentials(user=Depends(get_current_user), db=Depends(get_db)) -> list[CredentialResponse]:
    ...


@router.get("/{exchange}/balance")
async def get_balance(
    exchange: str, user=Depends(get_current_user), db=Depends(get_db)
) -> list["AccountBalance"]:
    """15번 문서 §15.4 — 재점검 라운드에서 16번 문서에 누락된 것을 발견해 추가.
    FD-3.2를 FD-12.2(get_adapter_for_user)로 경유해 호출."""
    ...


@router.get("/{exchange}/positions")
async def get_positions(
    exchange: str, user=Depends(get_current_user), db=Depends(get_db)
) -> list["Position"]:
    """15번 문서 §15.4 — 재점검 라운드에서 누락 발견, 추가. FD-3.3 경유."""
    ...


@router.get("/{exchange}/capabilities")
async def get_capabilities(
    exchange: str, user=Depends(get_current_user), db=Depends(get_db)
) -> "ExchangeCapability":
    """v1.10 신규(ADR-2026-08-28) — 이 사용자가 등록한 거래소가 실제 지원하는
    자산군 목록 등을 조회. ExchangeCredentialService.get_capabilities_for_user()
    위임. 17번 프론트엔드가 전략 편집기 UI를 자산군별로 조건부 렌더링하는 데 사용."""
    ...
```

```python
# src/services/exchange_credential_service.py
# STATUS: SCAFFOLD-READY
class ExchangeCredentialService:
    async def register(self, user_id: "UUID", body: CredentialRequest) -> "ExchangeCredential":
        """FD-12.1 — AES-256-GCM 암호화(07번 §7.3 키 재사용) 후 저장,
        저장 전 get_adapter_for_user()로 1회 검증 호출. "0번부터 재검토"
        라운드 추가: 검증 성공 후 check_withdrawal_permission() 호출로
        출금 권한 포함 여부 확인 — True면 ValueError("WITHDRAWAL_PERMISSION_DETECTED")
        발생시켜 저장 자체를 거부(02번 문서 "Adapter는 출금 기능을 포함하지
        않는다" 원칙의 실제 강제, 최소권한 원칙). None(확인불가)이면 경고
        문구와 함께 저장은 허용."""
        ...

    async def revoke(self, user_id: "UUID", credential_id: int) -> None: ...

    async def get_adapter_for_user(
        self, user_id: "UUID", exchange: str
    ) -> "ExchangeAdapter":
        """FD-12.2 — FD-3.1 개정 지점. user_id+exchange로 복호화된 자격증명을 찾아
        02번 ExchangeAdapter 인스턴스를 생성/캐싱(TTL 캐시, Draft). 이 메서드가
        4.10 멀티테넌시 격리 원칙의 실제 관문 — 여기서 키가 섞이면 플랫폼 전체가
        깨진다는 것을 구현 시 반드시 숙지."""
        ...

    async def get_capabilities_for_user(
        self, user_id: "UUID", exchange: str
    ) -> "ExchangeCapability":
        """v1.10 신규(ADR-2026-08-28) — get_adapter_for_user()로 얻은 Adapter의
        get_capabilities()를 그대로 반환하는 얇은 래퍼. 프론트엔드(17번)가
        전략 편집기에서 "이 거래소가 지금 뭘 지원하는지"에 따라 파생상품
        입력 필드를 조건부로 노출/비활성화하는 데 사용한다(02번 §2.0-A
        capability-gated 원칙의 UI 대응)."""
        ...

    async def check_withdrawal_permission(
        self, exchange: str, api_key: str, api_secret: str
    ) -> bool:
        """FD-12.1 — "0번부터 재검토" 라운드에서 참조만 되고 정의가 없던
        것을 발견해 추가. 02번 `ExchangeAdapter`(FROZEN 인접 아님, SCAFFOLD
        인터페이스)에는 이 메서드가 없다 — 출금 기능 자체를 이 시스템이
        아예 갖지 않는다는 02번 원칙에 따라 표준 인터페이스에 넣지 않고,
        등록 검증 시에만 쓰는 이 서비스 전용 헬퍼로 격리한다. 거래소별
        구현: Bitget은 API Key 조회 엔드포인트(예: GET account/apikey
        permissions, 실제 엔드포인트명은 착수 시 Bitget 공식 문서로 확정)
        응답의 권한 목록에서 "withdraw" 포함 여부 확인. **KIS 등 권한범위
        조회를 지원하지 않는 거래소는 이 메서드가 `None`을 반환**하고,
        FD-12.1 라우터가 그 경우 저장은 허용하되 경고 문구로 대체한다
        (02번 원문에 없는 자동판별 방법이 없으므로 fail-open이 아니라
        "확인 불가"를 명시적으로 사용자에게 알리는 방향— 완전 차단은
        오탐 시 정상 등록도 막을 수 있어 과도함)."""
        ...
```

---

## §16.3 FD-13 마켓플레이스 — `src/api/routers/marketplace.py`

```python
# STATUS: SCAFFOLD-READY
from decimal import Decimal
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header


router = APIRouter(prefix="/marketplace", tags=["marketplace"])


class ListingCreateRequest(BaseModel):
    strategy_id: str
    strategy_version: str
    price: Decimal


class ListingResponse(BaseModel):
    id: int
    strategy_id: str
    seller_user_id: "UUID"
    price: Decimal
    status: str  # DRAFT|PENDING_VERIFICATION|LISTED|DELISTED


class VerificationDecisionRequest(BaseModel):
    decision: str  # "APPROVE" | "REJECT"
    rejection_reason: str | None = None


class PurchaseResponse(BaseModel):
    purchase_id: int
    status: str
    risk_warning: bool = False               # FD-15.3 연동
    risk_warning_reason: str | None = None
    requires_explicit_consent: bool = False


@router.post("/listings", status_code=201)
async def create_listing(
    body: ListingCreateRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> ListingResponse:
    """FD-13.1 — 3개월 Paper Trading 이력 미달 시 즉시 거부(FD-13.1 예외상황)."""
    ...


@router.get("/listings")
async def list_listings(
    asset_class: "AssetClass | None" = None,  # v1.10: 01번 AssetClass enum, ADR-2026-08-28
    exchange: str | None = None,
    min_backtest_months: int | None = None,
    max_price: Decimal | None = None,
    page=Depends(pagination),
    db=Depends(get_db),
) -> PaginatedResponse:
    """FD-13.8 — status='LISTED'만 공개 노출. 15번 §15.1 페이지네이션 적용.
    기본 정렬은 리스팅 생성일이 아니라 검증통과일(FD-13.2 완료시각) 역순
    (14번 §14.4.3 — 재등록을 통한 상단노출 조작 방지), 동점 시 샤프비율
    내림차순 2차 정렬. 재점검 라운드에서 필터·정렬 파라미터 전체가 누락돼
    있었음을 발견해 추가(14번 §14.4가 처음부터 요구했던 것)."""
    ...


@router.get("/listings/{listing_id}")
async def get_listing_detail(listing_id: int, db=Depends(get_db)) -> ListingResponse:
    """15번 문서 §15.5 — 재점검 라운드에서 누락 발견, 추가. 리스팅 상세만 공개,
    전략 내부 로직(FSMStrategyConfig)은 미구매자에게 노출 안 함(10.3-B 블랙박스 원칙)."""
    ...


@router.post("/listings/{listing_id}/submit-verification")
async def submit_for_verification(
    listing_id: int, user=Depends(get_current_user), db=Depends(get_db)
) -> ListingResponse:
    """15번 문서 §15.5 — 재점검 라운드에서 누락 발견, 추가. FD-13.1(리스팅 생성,
    status=DRAFT)과 FD-13.2(검증) 사이의 명시적 전환 단계 — 판매자가 준비되면
    DRAFT→PENDING_VERIFICATION으로 스스로 제출(create_listing이 자동으로
    PENDING_VERIFICATION 진입시킨다는 기존 FD-13.1 서술과 다소 어긋나므로,
    착수 시 "자동 큐잉" vs "수동 제출" 중 하나로 확정 필요 — Draft로 표시)."""
    ...


@router.get("/my-purchases")
async def list_my_purchases(
    page=Depends(pagination), user=Depends(get_current_user), db=Depends(get_db)
) -> PaginatedResponse:
    """15번 문서 §15.5 — 재점검 라운드에서 누락 발견, 추가. 페이지네이션도 함께 적용."""
    ...


@router.post("/listings/{listing_id}/verify")
async def verify_listing(
    listing_id: int,
    body: VerificationDecisionRequest,
    verifier=Depends(get_current_verifier),
    db=Depends(get_db),
) -> ListingResponse:
    """FD-13.2/FD-18.1 — verifier_user_id != listing.seller_user_id 강제 검증(15.6
    이해상충 규칙), 위반 시 403."""
    ...


@router.post("/listings/{listing_id}/purchase", status_code=201)
async def purchase_listing(
    listing_id: int,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user=Depends(get_current_user),
    db=Depends(get_db),
) -> PurchaseResponse:
    """FD-13.3 — 자전거래(seller==buyer) 방지, FD-15.3 위험등급 대조 후
    risk_warning 필드 포함해 반환. 15번 문서 §15.1 Idempotency 원칙(금전 관련
    POST) 적용 — 재점검 라운드에서 누락 발견, 추가. 동일 키로 재요청 시
    신규 구매를 만들지 않고 기존 구매 결과를 그대로 반환(7.5 멱등성 원칙과
    동일 정신, FD-4.2-a와 같은 패턴)."""
    ...


class ReviewCreateRequest(BaseModel):
    rating: int  # 1~5
    comment: str | None = None


class ReviewResponse(BaseModel):
    review_id: int
    listing_id: int
    rating: int
    created_at: "datetime"


class DisputeCreateRequest(BaseModel):
    purchase_id: int
    reason: str


class DisputeResponse(BaseModel):
    dispute_id: int
    status: str  # OPEN | RESOLVED
    created_at: "datetime"


@router.post("/listings/{listing_id}/reviews", status_code=201)
async def create_review(
    listing_id: int, body: ReviewCreateRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> ReviewResponse:
    """FD-13.9 — "0번부터 재검토" 라운드에서 발견: 14번 문서 §14.2가 리뷰
    시스템을 처음부터 요구했고 17번 프론트엔드가 WriteReviewPage까지 만들어
    뒀는데, 정작 이 엔드포인트 자체가 없었음. 구매 이력 없으면 403,
    listing당 1회만 작성 가능(중복 시 400, `reviews` UNIQUE 제약과 이중 방어)."""
    ...


@router.get("/listings/{listing_id}/reviews")
async def list_reviews(
    listing_id: int, page=Depends(pagination), db=Depends(get_db)
) -> PaginatedResponse:
    """FD-13.9 — 리뷰 5건 미만이면 평균 별점 미표시(14.2.3 임계치), 개별
    원문은 건수 무관 항상 노출."""
    ...
```

```python
# src/api/routers/disputes.py
# STATUS: SCAFFOLD-READY
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/disputes", tags=["disputes"])


@router.post("", status_code=201)
async def submit_dispute(
    body: DisputeCreateRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> DisputeResponse:
    """FD-13.10 — "0번부터 재검토" 라운드에서 발견: FD-18.2(운영자 분쟁처리)는
    이미 있었지만, 애초에 구매자가 분쟁을 "제기"하는 이 엔드포인트가 없어서
    17번 프론트엔드 DisputeSubmitPage가 호출할 대상이 없었음. purchase_id가
    요청자 본인 구매가 아니면 403, 이미 OPEN 상태 분쟁이 있으면 400
    (04번 disputes 부분유니크인덱스와 이중 방어)."""
    ...
```

```python
# src/services/marketplace_service.py
# STATUS: SCAFFOLD-READY
class MarketplaceService:
    async def create_listing(self, seller_id: "UUID", body: ListingCreateRequest) -> "StrategyListing": ...

    async def verify(
        self, verifier_id: "UUID", listing_id: int, decision: str, reason: str | None
    ) -> "StrategyListing":
        """이해상충 검증은 여기서 최종 확인(라우터의 Depends는 role만 확인,
        listing별 판매자 대조는 데이터를 봐야 하므로 서비스 레벨 책임)."""
        ...

    async def purchase(self, buyer_id: "UUID", listing_id: int) -> "PurchaseResult":
        """FD-13.3+FD-13.7(중개수수료 계산)+FD-15.3(위험등급 대조) 오케스트레이션.
        price_paid에서 platform_commission_amount를 계산해 seller_payout_amount
        확정(14.1 갱신분, 04번 DB스키마)."""
        ...

    async def grant_execution_access(self, purchase_id: int) -> "PurchasedStrategyAccess":
        """FD-13.4 — owner_user_id는 원 제작자 유지, 실행 컨텍스트만 구매자 것으로
        분리 생성(실제로는 strategy_purchases에 이미 있는 buyer_user_id로 접근권한을
        확인하는 것뿐, 별도 테이블 신설 불필요 — Draft, 착수 시 확정). 재점검 라운드
        수정: 반환값이 막연한 "StrategyInstance"가 아니라, FD-16.1
        `ExecutionCreateRequest.strategy_id`에 **그대로 넣을 수 있는 문자열**임을
        명시(PurchasedStrategyAccess.strategy_id) — 구매 흐름과 실행 흐름 사이의
        인터페이스가 새 타입이 아니라 이미 있는 strategy_id 하나로 이어진다."""
        ...

    async def search_listings(
        self,
        asset_class: "AssetClass | None",  # v1.10: 01번 AssetClass enum, ADR-2026-08-28
        exchange: str | None,
        min_backtest_months: int | None,
        max_price: "Decimal | None",
    ) -> list["StrategyListing"]:
        """FD-13.8 — "0번부터 재검토" 라운드 신설. 필터 적용 후 검증통과일
        역순 정렬(생성일 아님 — 조작 방지), 동점 시 샤프비율 내림차순."""
        ...

    async def create_review(
        self, listing_id: int, reviewer_id: "UUID", body: "ReviewCreateRequest"
    ) -> "Review":
        """FD-13.9 — 구매 이력 확인(자전거래 방지 원칙 재사용) 후
        `reviews` INSERT. UNIQUE(listing_id, reviewer_user_id) 위반 시
        ValueError("DUPLICATE_REVIEW") 발생(라우터가 400으로 변환)."""
        ...

    async def list_reviews(self, listing_id: int) -> tuple[list["Review"], "Decimal | None"]:
        """FD-13.9 — (리뷰목록, 평균별점) 반환. 리뷰 5건 미만이면 평균별점은
        None(14.2.3 임계치 — 화면에 "리뷰 부족(N건)"으로 표시)."""
        ...


class DisputeService:
    """FD-13.10/FD-18.2 전담 — 접수(사용자)와 조회·처리(운영자) 양쪽 다
    이 클래스가 담당(같은 `disputes` 테이블을 다루므로 서비스도 통합).
    "0번부터 재검토" 라운드에서 발견: FD-18.2는 조회·처리 메서드만 있었고
    접수(submit) 메서드가 없었음."""

    async def submit(
        self, submitted_by: "UUID", body: "DisputeCreateRequest"
    ) -> "Dispute":
        """FD-13.10 — purchase_id가 submitted_by 본인 구매인지 확인(아니면
        PermissionError, 라우터가 403으로 변환), 동일 purchase_id에 이미
        OPEN 분쟁이 있으면 ValueError("DUPLICATE_DISPUTE")(400으로 변환,
        04번 부분유니크인덱스와 이중 방어)."""
        ...


class PurchaseResult(BaseModel):
    """MarketplaceService.purchase() 내부 결과 — 재점검 라운드에서 참조만
    되고 정의가 없던 것을 발견해 추가. 라우터가 이 값을 그대로 PurchaseResponse
    로 변환해 반환한다(§16.0 "내부 도메인 객체 vs API 응답 모델" 분리 패턴)."""
    purchase_id: int
    status: str
    price_paid: "Decimal"
    platform_commission_amount: "Decimal"
    seller_payout_amount: "Decimal"
    risk_warning: bool
    risk_warning_reason: str | None = None


class PurchasedStrategyAccess(BaseModel):
    """FD-13.4 반환 타입 — 재점검 라운드에서 미정의 "StrategyInstance"를
    대체. FD-16.1이 그대로 받는 strategy_id가 핵심 필드."""
    strategy_id: str
    strategy_version: str
    purchase_id: int
    granted_at: "datetime"
```

---

## §16.4 FD-14 전략 편집기 — `src/api/routers/strategy_builder.py`

```python
# STATUS: SCAFFOLD-READY
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from src.data.models.strategy_fsm import FSMStrategyConfig


router = APIRouter(tags=["strategy-builder"])


class IndicatorSpec(BaseModel):
    name: str          # "RSI", "MACD" 등 — TA-Lib 함수명 그대로 사용(FD-14.1)
    params: dict        # {"period": 14} 등


class ConditionSpec(BaseModel):
    indicator: IndicatorSpec
    operator: str        # "<", ">", "<=", ">="
    value: float


class StrategyCreateRequest(BaseModel):
    target_asset: str
    exchange: str
    entry_condition: ConditionSpec
    exit_condition: ConditionSpec
    stop_loss_pct: float | None = None

    # v1.10(ADR-2026-08-28) 다자산군 확장 — 크립토/현물 전략은 전부 None 유지.
    asset_class: "AssetClass | None" = None  # None이면 서비스 레벨에서 CRYPTO로 간주(하위호환)
    option_type: str | None = None            # "CALL" | "PUT" — 옵션 전략만
    strike_price: float | None = None
    expiry_date: "date | None" = None
    underlying_symbol: str | None = None


class StrategyResponse(BaseModel):
    strategy_id: str
    version: str
    status: str  # 9.1 lifecycle_status
    fsm_definition: FSMStrategyConfig


class IndicatorListResponse(BaseModel):
    indicators: list[str]  # TA-Lib 지원 지표 전체 목록


class PreviewResponse(BaseModel):
    period: str
    signals: list[dict]  # [{timestamp, type, price}]
    disclaimer: str = "이것은 정식 백테스트가 아닙니다."


@router.get("/indicators")
async def list_indicators() -> IndicatorListResponse:
    """FD-14.1 — IndicatorService.available_indicators() 위임, TA-Lib 함수 목록 그대로 노출."""
    ...


@router.get("/indicators/{name}/compute")
async def compute_indicator(
    name: str, symbol: str, timeframe: str, period: int = 14, db=Depends(get_db)
) -> dict:
    """FD-14.1 — 최소 캔들 수 미달 시 빈 배열 반환(예외 아님, 안내 메시지 포함)."""
    ...


@router.post("/strategies", status_code=201)
async def create_strategy(
    body: StrategyCreateRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> StrategyResponse:
    """FD-14.2/14.3 — StrategyBuilderService.compile_and_save() 위임.
    컴파일 실패(모순 조건) 시 400 + STRATEGY_CONDITION_INVALID."""
    ...


@router.get("/strategies/{strategy_id}/preview")
async def preview_strategy(
    strategy_id: str, user=Depends(get_current_user), db=Depends(get_db)
) -> PreviewResponse:
    """FD-14.4 — 저장 여부와 무관, 경량 즉석 계산."""
    ...


@router.get("/strategies/{strategy_id}")
async def get_strategy(
    strategy_id: str, user=Depends(get_current_user), db=Depends(get_db)
) -> StrategyResponse:
    """소유자만 조회 가능(9.1 상태 조회)."""
    ...
```

```python
# src/services/strategy_builder_service.py
# STATUS: SCAFFOLD-READY
class ConditionCompiler:
    """FD-14.2 — 사용자의 진입/청산/손절 조건 3개를 9.11 FSM 상태·전이로 변환.
    이 클래스는 FSMStrategyConfig(데이터)만 생성한다 — FD-8(FROZEN)의 판단
    로직을 절대 호출하지 않는다(03번 §3.9 Zone 경계)."""

    def compile(
        self, entry: ConditionSpec, exit: ConditionSpec, stop_loss_pct: float | None
    ) -> FSMStrategyConfig:
        """모순되는 상태 전이 발견 시 CompilationError 발생(라우터가 400으로 변환)."""
        ...


class StrategyBuilderService:
    def __init__(self, compiler: ConditionCompiler, indicator_service: "IndicatorService"): ...

    async def compile_and_save(
        self, owner_id: "UUID", body: StrategyCreateRequest
    ) -> "Strategy":
        """FD-14.3 — 저장 시 lifecycle_status='GENERATED' 강제 진입(9.1),
        created_via='EDITOR'로 태깅(9.2 생성방식 추적, 04번 DB스키마)."""
        ...

    async def preview(self, strategy_id: str) -> PreviewResponse:
        """FD-14.4 — 정식 백테스트(9.3)와 별개 경량 계산, 최근 3개월 캔들만 사용."""
        ...


class IndicatorService:
    """FD-14.1 — TA-Lib 어댑터. 이 클래스는 지표 '값'만 계산하고 매매 판단은
    하지 않는다(FD-8과의 경계, 정책문서 준수의 코드 레벨 실증)."""

    def available_indicators(self) -> list[str]: ...

    async def compute(
        self, name: str, symbol: str, timeframe: str, params: dict
    ) -> list[float]:
        """최소 캔들 수 미달 시 빈 리스트 반환(예외 아님)."""
        ...
```

---

## §16.5 FD-15 투자자 적합성평가 — `src/api/routers/suitability.py`

```python
# STATUS: SCAFFOLD-READY
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends


router = APIRouter(prefix="/users/me", tags=["suitability"])


class RiskAssessmentRequest(BaseModel):
    investment_experience_years: int
    capital_at_risk_pct: float
    max_acceptable_loss_pct: float
    investment_horizon: str   # "SHORT_TERM" | "LONG_TERM"
    liquidity_needs: str      # "LOW" | "MEDIUM" | "HIGH"


class RiskProfileResponse(BaseModel):
    risk_profile: str  # "안정형"|"중립형"|"공격형" — Draft 등급체계
    assessed_at: datetime
    next_reassessment_due: datetime


@router.post("/risk-assessment", status_code=201)
async def submit_assessment(
    body: RiskAssessmentRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> RiskProfileResponse:
    """FD-15.1 — 회원가입 직후 필수 게이트. SuitabilityService.assess() 위임."""
    ...


@router.get("/risk-profile")
async def get_risk_profile(user=Depends(get_current_user), db=Depends(get_db)) -> RiskProfileResponse:
    ...


@router.get("/risk-profile/history")
async def get_risk_profile_history(
    user=Depends(get_current_user), db=Depends(get_db)
) -> list[RiskProfileResponse]:
    """FD-15.2 — risk_profile_history 전체 이력(삭제 없음, 4.6-A 원칙과 동일 정신)."""
    ...
```

```python
# src/services/suitability_service.py
# STATUS: SCAFFOLD-READY
class SuitabilityService:
    def score(self, answers: RiskAssessmentRequest) -> str:
        """FD-15.1 — Draft 점수화 로직. 문항별 가중치는 config/suitability_scoring.yaml
        (신규, Draft — 8.2-B risk_policy.yaml과 동일 원칙: 하드코딩 금지)로 관리."""
        ...

    async def assess(self, user_id: "UUID", body: RiskAssessmentRequest) -> "RiskProfileHistory":
        """FD-15.2 — risk_profile_history INSERT + users.risk_profile UPDATE.
        재평가로 등급이 나빠진 경우 즉시 MatchingWarningService.recheck_all() 트리거
        (FD-15.2 예외상황)."""
        ...


class MatchingWarningService:
    """FD-15.3 — 강제 차단이 아니라 경고+동의. 이 클래스는 bool을 반환할 뿐
    구매/배포를 직접 막지 않는다 — 호출부(FD-13.3/FD-14.3/FD-16.2)가 이 결과를
    보고 판단한다."""

    async def check_mismatch(
        self, user_risk_profile: str, target_risk_indicator: dict
    ) -> tuple[bool, str | None]:  # (mismatch: bool, reason: str | None)
        ...

    async def recheck_all(self, user_id: "UUID") -> list["StrategyExecution"]:
        """FD-15.2 예외상황 — 재평가로 등급이 나빠졌을 때 기존 RUNNING 실행 중
        새 등급과 불일치하는 것을 찾아 FD-17 알림 발행."""
        ...
```

---

## §16.6 FD-16 전략 실행 제어판 — `src/api/routers/executions.py`

```python
# STATUS: SCAFFOLD-READY
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header


router = APIRouter(prefix="/strategy-executions", tags=["executions"])


class ExecutionCreateRequest(BaseModel):
    strategy_id: str
    exchange: str
    mode: str  # "PAPER" | "LIVE"
    allocated_capital: Decimal
    currency: str = "KRW"


class ExecutionResponse(BaseModel):
    execution_id: "UUID"
    status: str  # PENDING_APPROVAL|RUNNING|PAUSED|RETIRED
    mode: str
    approval_request_id: "UUID | None" = None
    message: str | None = None


class ExecutionActionRequest(BaseModel):
    liquidation: str | None = None  # RETIRE 시에만: IMMEDIATE_MARKET|KEEP_POSITIONS


class ExecutionMonitorResponse(BaseModel):
    execution_id: "UUID"
    strategy_id: str
    status: str
    paused_by: str | None = None  # "USER" | "SAFETY_LAYER" — status="PAUSED"일 때만
    # 17번 프론트엔드 문서(§17.5.2) 검토 중 발견 — Watchdog이 멈춘 건지 사용자가
    # 멈춘 건지 UI가 구분 못 하면 위험한 오해를 부를 수 있어 필드 신설(v1.3 후속)
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    max_drawdown_pct: Decimal


class ConvertToLiveRequest(BaseModel):
    allocated_capital: Decimal
    exchange: str


@router.post("", status_code=201)
async def create_execution(
    body: ExecutionCreateRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user=Depends(get_current_user),
    db=Depends(get_db),
) -> ExecutionResponse:
    """FD-16.1/16.2 — 8.2-B 상한 검증(초과 시 400), LIVE+임계초과 시 202+승인요청ID.
    15번 문서 §15.1 Idempotency 적용(자본 배분이 걸린 요청 — 재점검 라운드 추가)."""
    ...


@router.post("/{execution_id}/start")
async def start_execution(
    execution_id: "UUID", user=Depends(get_current_user), db=Depends(get_db)
) -> ExecutionResponse:
    """FD-16.3 시작 — Watchdog/Circuit Breaker가 이미 PAUSED 상태면 409
    EXECUTION_BLOCKED_BY_SAFETY_LAYER(8.6-B 우선순위, 절대 우회 불가)."""
    ...


@router.post("/{execution_id}/pause")
async def pause_execution(
    execution_id: "UUID", user=Depends(get_current_user), db=Depends(get_db)
) -> ExecutionResponse:
    ...


@router.post("/{execution_id}/retire")
async def retire_execution(
    execution_id: "UUID",
    body: ExecutionActionRequest,
    user=Depends(get_current_user),
    db=Depends(get_db),
) -> ExecutionResponse:
    ...


@router.get("")
async def list_executions(
    user=Depends(get_current_user), db=Depends(get_db)
) -> list[ExecutionMonitorResponse]:
    """FD-16.4."""
    ...


@router.post("/{execution_id}/convert-to-live", status_code=201)
async def convert_to_live(
    execution_id: "UUID",
    body: ConvertToLiveRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user=Depends(get_current_user),
    db=Depends(get_db),
) -> ExecutionResponse:
    """FD-16.5 — 원본 PAPER 실행은 종료하지 않고 신규 LIVE 실행 생성
    (converted_from_execution_id로 연결, 04번 DB스키마). Idempotency 적용
    (재점검 라운드 추가, create_execution과 동일 경로 재사용이므로 동일 원칙)."""
    ...
```

```python
# src/services/execution_service.py
# STATUS: SCAFFOLD-READY

class RiskPolicyGate:
    """8.2-B 상한 검증 전담 — **FROZEN RiskEngine(03번 §3.7)이 아니다.** 이 클래스는
    `config/risk_policy.yaml`의 선언적 수치(전략별 최대 배분 비중 등)를 읽어
    단순 산술 비교만 한다 — "이 주문을 승인할지" 판단이 아니라 "이 배분 요청이
    설정값을 넘는지"만 본다. FD-8/FROZEN RiskEngine.check()와 역할이 다르다는
    것을 재점검 라운드에서 재확인(21.2 Zone 판정 재검증 원칙에 따라 명시)."""

    def __init__(self, policy_path: str = "config/risk_policy.yaml"): ...

    async def check_allocation(
        self,
        user_id: "UUID",
        requested_capital: "Decimal",
        account_balance: "Decimal",
        is_certified: bool,  # strategies.certified_badge — 재점검 라운드 추가
    ) -> tuple[bool, str | None]:  # (approved, rejection_reason)
        """07번 §7.2 risk_policy.yaml — is_certified=False면
        strategy_allocation.unverified_max_pct(Draft 10%), True면
        certified_level4_max_pct(Draft 25%) 적용."""
        ...


class ApprovalRequest(BaseModel):
    """FD-10.1 승인 요청 — 16번 §16.6에서 참조만 되고 정의가 없던 것을
    재점검 라운드에서 발견해 추가."""
    approval_request_id: "UUID"
    trigger_source: str
    context: dict
    requested_action: str
    status: str  # PENDING | APPROVED | REJECTED | EXPIRED
    expires_at: "datetime"


class ApprovalService:
    """FD-10.1 Critical Risk 승인 요청 생성 전담 — 16번 §16.6(ExecutionService)이
    참조만 하고 정의가 없던 것을 재점검 라운드에서 발견해 추가."""

    async def create_request(
        self, trigger_source: str, context: dict, requested_action: str
    ) -> ApprovalRequest:
        """FD-10.1 — 60초 타이머 시작, 서로 다른 인증계정 순차서명 요구.
        FD-17(알림)에 approval.request.created 이벤트 발행까지 포함."""
        ...

    async def get_status(self, approval_request_id: "UUID") -> str: ...


class SafetyLayerStatusProvider:
    """FD-9 현재 상태 조회 전담 — Watchdog/Circuit Breaker가 특정 실행을 이미
    막고 있는지 확인. 16번 §16.6이 참조만 하고 정의가 없던 것을 재점검
    라운드에서 발견해 추가. "클로드 코드 구현가능성" 라운드에서 실제 조회
    대상 확정: ①개별 실행 차단은 `strategy_executions.status='PAUSED' AND
    paused_by='SAFETY_LAYER'`(FD-16.3, 신규 테이블 불필요) — 자기 실행이
    Watchdog에 의해 멈췄는지. ②시스템 전역 차단은 `system_safety_state.
    circuit_breaker_level`(04번 신규, FD-9.4) — halted/emergency면 신규
    실행 자체를 막음(개별 실행과 무관하게 전체 차단)."""

    async def is_blocked(self, execution_id: "UUID") -> tuple[bool, str | None]:
        """(blocked, reason) — ①system_safety_state.circuit_breaker_level이
        halted/emergency면 즉시 True, "시스템 전역 거래 제한" 사유 반환
        (execution_id 조회조차 불필요 — 시스템 레벨이 우선). ②아니면
        strategy_executions에서 해당 execution의 paused_by 확인."""
        ...


class ExecutionService:
    def __init__(
        self,
        risk_gate: RiskPolicyGate,          # 8.2-B 상한 검증 (FROZEN Risk 계층을 직접
                                             # 호출하지 않고, config/risk_policy.yaml을
                                             # 읽는 별도 게이트 — 03번 §3.9 Zone 경계 준수)
        approval_service: ApprovalService,  # FD-10.1 연동
        safety_layer_status: SafetyLayerStatusProvider,  # FD-9 현재 상태 조회
    ): ...

    async def create(self, user_id: "UUID", body: ExecutionCreateRequest) -> "StrategyExecution":
        """FD-16.1/16.2 오케스트레이션. body.exchange=="kis" and body.mode=="LIVE"
        면 즉시 거부(06번 §6.1 Phase 1 스콥 — KIS는 인터페이스만, 재점검 라운드
        추가). risk_gate.check_allocation()으로 상한 검증
        → 초과 시 400. LIVE + 자동화Level 1~3(9.10)이면 approval_service로 승인요청
        생성 후 status=PENDING_APPROVAL."""
        ...

    async def start(self, execution_id: "UUID") -> "StrategyExecution":
        """FD-16.3 — safety_layer_status.is_blocked(execution_id) 확인이 최우선
        (8.6-B 원칙 코드 레벨 실증). True면 EXECUTION_BLOCKED_BY_SAFETY_LAYER 예외."""
        ...

    async def pause(self, execution_id: "UUID") -> "StrategyExecution": ...

    async def retire(
        self, execution_id: "UUID", liquidation: str | None
    ) -> "StrategyExecution":
        """기본값 KEEP_POSITIONS(FD-16.3 Draft 원칙 — 의도치 않은 강제청산 방지)."""
        ...

    async def convert_to_live(
        self, execution_id: "UUID", body: ConvertToLiveRequest
    ) -> "StrategyExecution":
        """FD-16.5 — self.create()를 mode=LIVE로 재사용, converted_from_execution_id
        연결. 승인 절차 생략 없음(create()의 임계치 검증 로직 그대로 재사용)."""
        ...

    async def monitor_all(self, user_id: "UUID") -> list[ExecutionMonitorResponse]:
        """FD-16.4 — strategy_executions + orders/positions 조인."""
        ...
```

---

## §16.7 FD-17 알림 시스템 — `src/core/notifications/`

```python
# src/core/notifications/gateway.py
# STATUS: SCAFFOLD-READY
from enum import Enum

class NotificationChannel(str, Enum):
    EMAIL = "EMAIL"
    PUSH = "PUSH"
    IN_APP = "IN_APP"


class ChannelPolicy(BaseModel):
    """FD-17.2 — 강제 채널은 화이트리스트로만 지정, 미지정 이벤트는 기본값
    (이메일만, 사용자가 끌 수 있음)으로 fail-safe."""
    channels: list[NotificationChannel]
    user_overridable: bool


# FD-17.2 정책 테이블(Draft: 코드 상수, 변경 빈도 높아지면 DB로 이전)
CHANNEL_POLICIES: dict[str, ChannelPolicy] = {
    "approval.request.created": ChannelPolicy(
        channels=[NotificationChannel.EMAIL, NotificationChannel.PUSH], user_overridable=False
    ),
    "watchdog.decision.triggered": ChannelPolicy(
        channels=[NotificationChannel.EMAIL, NotificationChannel.PUSH], user_overridable=False
    ),
    "risk.circuit_breaker.reactivation_requested": ChannelPolicy(
        # "다시 0번부터" 라운드 발견 — 기능설계문서 FD-17.2엔 있었으나 이
        # 딕셔너리엔 등록이 안 돼 있었음(FD-9.4b 신설 시 문서만 갱신하고
        # 코드 반영을 놓침). human_approval.requested와 동일 취급.
        channels=[NotificationChannel.EMAIL, NotificationChannel.PUSH], user_overridable=False
    ),
    "security.withdrawal_whitelist.added": ChannelPolicy(
        # "다시 0번부터" 라운드 신설 — FD-11.5(비상출금 화이트리스트 등록),
        # 계정 탈취 시 공격자의 무단 등록을 사용자가 즉시 알아야 하는
        # 보안 이벤트라 강제 채널로 분류.
        channels=[NotificationChannel.EMAIL, NotificationChannel.PUSH], user_overridable=False
    ),
    "execution.safety_block.applied": ChannelPolicy(
        channels=[NotificationChannel.IN_APP], user_overridable=False
    ),
    "risk_profile.match.warned": ChannelPolicy(
        channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL], user_overridable=True
    ),
    "marketplace.purchase.requested": ChannelPolicy(
        channels=[NotificationChannel.EMAIL], user_overridable=True
    ),
    "marketplace.payment.confirmed": ChannelPolicy(
        # 재점검 라운드 신설 — FD-18.5b(결제확인) 시점, 舊 "purchase_confirmed"에서
        # 분리(구매신청과 실행가능시점을 혼동하던 것을 정정)
        channels=[NotificationChannel.EMAIL], user_overridable=True
    ),
    "strategy.verification.completed": ChannelPolicy(
        channels=[NotificationChannel.EMAIL], user_overridable=True
    ),
}
DEFAULT_POLICY = ChannelPolicy(channels=[NotificationChannel.EMAIL], user_overridable=True)


class NotificationGateway:
    """FD-17.1 — FD-6(Event Bus)의 구독자로 등록된다. 새 인프라를 만들지 않는다는
    설계 원칙(정책문서 17.9-A)을 코드 레벨에서 실증 — 이 클래스는 독자적인 폴링 루프가 없다."""

    def __init__(self, event_bus: "InProcessEventBus", channel_adapters: dict[NotificationChannel, "ChannelAdapter"]):
        """이벤트버스에 self._on_event를 CRITICAL Handler로 구독 등록(FD-6.2)."""
        ...

    async def _on_event(self, event_type: str, payload: dict, user_id: "UUID") -> None:
        """FD-17.1 처리단계 그대로 — policy 조회 → 채널별 발송 → notifications
        테이블 기록 → 실패 시 FD-6.3 CRITICAL 재시도(5회) → audit_log."""
        ...

    def get_policy(self, event_type: str) -> ChannelPolicy:
        return CHANNEL_POLICIES.get(event_type, DEFAULT_POLICY)


class ChannelAdapter(ABC):
    """이메일/푸시 어댑터 공통 인터페이스 — 02번 ExchangeAdapter와 동일한 설계
    패턴(구현체는 격리, 상위는 인터페이스만 앎)."""

    @abstractmethod
    async def send(self, user_id: "UUID", event_type: str, payload: dict) -> bool: ...


class EmailChannelAdapter(ChannelAdapter):
    """Draft: SMTP 또는 트랜잭션 메일 서비스, 착수 시 확정."""
    ...


class PushChannelAdapter(ChannelAdapter):
    """FD-21.1 device_tokens 참조 — APNs(iOS)/FCM(Android)."""
    ...
```

```python
# src/api/routers/notifications.py
# STATUS: SCAFFOLD-READY
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(tags=["notifications"])


class NotificationPreferencesRequest(BaseModel):
    marketplace_purchase_email: bool | None = None
    # 재점검 라운드 확인: 이 필드 하나가 marketplace.purchase.requested(FD-13.3)
    # 와 marketplace.payment.confirmed(FD-18.5b) 이벤트 둘 다 제어한다 —
    # 별도 필드로 쪼개지 않는다(정책문서 17.9-A 과잉설계 방지, 사용자 입장에서 "구매
    # 관련 이메일"은 하나의 토글이면 충분).
    verification_result_email: bool | None = None
    risk_mismatch_email: bool | None = None
    # human_approval_requested 등 강제 채널 필드는 여기 스키마 자체에 없음(FD-17.4 이중방어)


@router.get("/notifications")
async def get_notification_history(
    event_type: str | None = None,
    page=Depends(pagination),
    user=Depends(get_current_user),
    db=Depends(get_db),
) -> PaginatedResponse:
    """FD-17.3. 페이지네이션 적용(재점검 라운드 추가) — 알림 이력은 사용자당
    누적량이 많아질 수 있음."""
    ...


@router.get("/users/me/notification-preferences")
async def get_preferences(user=Depends(get_current_user), db=Depends(get_db)) -> NotificationPreferencesRequest:
    ...


@router.put("/users/me/notification-preferences")
async def update_preferences(
    body: NotificationPreferencesRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> NotificationPreferencesRequest:
    """FD-17.4 — 요청 스키마 자체에 강제 필드가 없으므로 Pydantic이 1차 방어,
    혹시 추가 필드가 섞여 오면 FastAPI가 기본 설정상 무시(model_config 확인 필요)."""
    ...
```

---

## §16.8 FD-18 운영자 도구 — `src/api/routers/admin.py`

```python
# STATUS: SCAFFOLD-READY
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header


router = APIRouter(prefix="/admin", tags=["admin"])


class DisputeResolveRequest(BaseModel):
    decision: str  # "NORMAL_RISK_REALIZATION" | "DELISTED_AND_REFUND"
    reason: str


class UserStatusUpdateRequest(BaseModel):
    new_status: str  # "ACTIVE" | "SUSPENDED" — 그 외 값은 400


@router.get("/verification-queue")
async def get_verification_queue(
    verifier=Depends(get_current_verifier), db=Depends(get_db)
) -> list["ListingResponse"]:
    """FD-18.1 — verifier_user_id != seller_user_id 필터 적용."""
    ...


@router.get("/disputes")
async def list_disputes(
    status: str | None = None,
    page=Depends(pagination),
    admin=Depends(get_current_admin),
    db=Depends(get_db),
) -> PaginatedResponse:
    """페이지네이션 적용(재점검 라운드 추가)."""
    ...


@router.get("/disputes/{dispute_id}")
async def get_dispute(
    dispute_id: int, admin=Depends(get_current_admin), db=Depends(get_db)
) -> dict:
    """FD-18.2 — 14.5.2 근거자료(검증체크리스트, audit_log 시점고정) 조인 반환."""
    ...


@router.post("/disputes/{dispute_id}/resolve")
async def resolve_dispute(
    dispute_id: int,
    body: DisputeResolveRequest,
    admin=Depends(get_current_admin),
    db=Depends(get_db),
) -> dict:
    ...


@router.get("/users")
async def list_users(
    email: str | None = None,
    page=Depends(pagination),
    admin=Depends(get_current_admin),
    db=Depends(get_db),
) -> PaginatedResponse:
    """페이지네이션 적용(재점검 라운드 추가)."""
    ...


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: "UUID",
    body: UserStatusUpdateRequest,
    admin=Depends(get_current_admin),
    db=Depends(get_db),
) -> "UserResponse":
    """FD-18.3 — DELETED/PENDING_DELETION 요청 시 400(FD-11.4 전용 절차 안내)."""
    ...


@router.post("/users/{user_id}/suspend-seller")
async def suspend_seller(
    user_id: "UUID", admin=Depends(get_current_admin), db=Depends(get_db)
) -> dict:
    """FD-18.4 — 멱등 처리(이미 정지된 경우 에러 아님)."""
    ...


@router.get("/purchases")
async def list_pending_purchases(
    status: str = "PENDING_PAYMENT",
    page=Depends(pagination),
    admin=Depends(get_current_admin),
    db=Depends(get_db),
) -> PaginatedResponse:
    """FD-18.5a — 재점검 라운드에서 발견: confirm-payment 라우터가 참조하는
    "결제 대기 탭"을 채울 목록 조회 API가 빠져있었음. 페이지네이션 적용."""
    ...


@router.post("/purchases/{purchase_id}/confirm-payment")
async def confirm_payment(
    purchase_id: int,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin=Depends(get_current_admin),
    db=Depends(get_db),
) -> dict:
    """FD-18.5b — 다양한 각도 재점검 라운드에서 발견: strategy_purchases.
    payment_status 컬럼이 있는데 이걸 바꾸는 API가 없었음(고아 컬럼).
    CONFIRMED 전환 시 FD-13.4(실행 연동)가 이 시점에 트리거되도록
    MarketplaceService.grant_execution_access()를 호출. 금전 관련 POST라
    Idempotency-Key 적용(15번 §15.1), 처리 완료 시 audit_log(FD-7.2) 기록
    필수(8.10 원칙 — 둘 다 같은 라운드에서 누락 발견해 추가)."""
    ...
```

```python
# src/services/admin_service.py
# STATUS: SCAFFOLD-READY
class AdminService:
    async def get_verification_queue(self, verifier_id: "UUID") -> list["StrategyListing"]: ...

    async def resolve_dispute(
        self, admin_id: "UUID", dispute_id: int, decision: str, reason: str
    ) -> "Dispute":
        """14.5.2 절차를 코드로 옮긴 것 — 새 판단 로직 없음. audit_log 기록 필수.
        "0번부터 재검토" 라운드 메모: 이 메서드와 신설 DisputeService.submit()은
        같은 `disputes` 테이블을 다루는 한 쌍(제출은 사용자, 처리는 운영자) —
        착수 시 두 메서드를 하나의 DisputeService로 합치고 AdminService는
        위임만 하는 형태로 정리 권장(지금은 문서 구조상 분리 표기, 실제
        코드에서는 중복 클래스 두지 않음)."""
        ...

    async def update_user_status(self, user_id: "UUID", new_status: str) -> "User":
        if new_status not in ("ACTIVE", "SUSPENDED"):
            raise BusinessRuleViolationError(
                "ADMIN_STATUS_TRANSITION_INVALID",
                "DELETED/PENDING_DELETION 전이는 FD-11.4 탈퇴 절차 전용입니다.",
            )  # 재점검 라운드에서 순수 ValueError → MihwaError 계층으로 정정
        ...

    async def suspend_seller(self, user_id: "UUID") -> "User": ...

    async def confirm_payment(
        self, purchase_id: int, marketplace_service: "MarketplaceService"
    ) -> "StrategyPurchase":
        """FD-18.5b — payment_status: PENDING_PAYMENT → CONFIRMED 전이 후
        marketplace_service.grant_execution_access() 호출(FD-13.4 트리거).
        전이 성공 시 audit_log(FD-7.2) INSERT 필수 — 8.10 원칙, 재점검
        라운드에서 누락 발견."""
        ...
```

---

## §16.9 FD-19 포트폴리오 관리 — `src/api/routers/portfolio.py`

```python
# STATUS: SCAFFOLD-READY
from decimal import Decimal
from pydantic import BaseModel
from fastapi import APIRouter, Depends


router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class PortfolioAllocation(BaseModel):
    strategy_id: str
    allocated_capital: Decimal
    current_value: Decimal
    pnl_pct: Decimal
    weight_pct: Decimal


class PortfolioResponse(BaseModel):
    allocations: list[PortfolioAllocation]
    unallocated_cash_pct: Decimal
    total_unrealized_pnl: Decimal


class RebalanceAdjustment(BaseModel):
    execution_id: "UUID"
    new_allocated_capital: Decimal


class RebalanceRequest(BaseModel):
    adjustments: list[RebalanceAdjustment]


class RebalanceResponse(BaseModel):
    adjusted: int
    pending_approval: int
    approval_request_ids: list["UUID"] = []


@router.get("")
async def get_portfolio(user=Depends(get_current_user), db=Depends(get_db)) -> PortfolioResponse:
    """FD-19.1 — RUNNING/PAUSED 실행이 없으면 unallocated_cash_pct=100(정상 상태)."""
    ...


@router.put("/rebalance")
async def rebalance(
    body: RebalanceRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> RebalanceResponse:
    """FD-19.2 — 배분 감소는 기존 포지션 강제청산 없이 신규진입만 제한.
    임계치 초과분은 FD-16.2와 동일하게 승인요청 생성."""
    ...
```

```python
# src/services/portfolio_service.py
# STATUS: SCAFFOLD-READY
class PortfolioService:
    def __init__(self, execution_service: "ExecutionService"): ...

    async def aggregate(self, user_id: "UUID") -> PortfolioResponse:
        """FD-19.1 — strategy_executions + 거래소별 잔고(FD-3.2) 조인 집계."""
        ...

    async def rebalance(self, user_id: "UUID", body: RebalanceRequest) -> RebalanceResponse:
        """FD-19.2 — 각 조정에 execution_service의 8.2-B 상한 검증 재사용,
        감소분은 '신규 진입 금지' 플래그만 세팅(청산 트리거 없음)."""
        ...
```

---

## §16.10 FD-20 운용보고서 — `src/api/routers/reports.py`

```python
# STATUS: SCAFFOLD-READY
from datetime import date
from decimal import Decimal
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query


router = APIRouter(prefix="/reports", tags=["reports"])


class StrategyContribution(BaseModel):
    strategy_id: str
    contribution_pct: Decimal


class DailyPnl(BaseModel):
    date: date
    cumulative_pnl: Decimal


class ReportResponse(BaseModel):
    period: str
    total_return_pct: Decimal | None = None
    win_rate_pct: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    trade_count: int
    by_strategy: list[StrategyContribution] = []
    daily_pnl_series: list[DailyPnl] = []
    message: str | None = None  # trade_count=0일 때 안내(FD-20.1 예외상황)


@router.get("")
async def get_report(
    period_start: date = Query(...),
    period_end: date = Query(...),
    execution_id: "UUID | None" = None,
    user=Depends(get_current_user),
    db=Depends(get_db),
) -> ReportResponse:
    """FD-20.1/20.2 — 화면 렌더링용 통합 응답(다운로드는 Phase 1 스콥 밖, Draft)."""
    ...
```

```python
# src/services/report_service.py
# STATUS: SCAFFOLD-READY
class ReportService:
    async def generate(
        self, user_id: "UUID", period_start: "date", period_end: "date", execution_id: "UUID | None"
    ) -> ReportResponse:
        """FD-20.1 — 별도 저장 없이 매 요청 즉석 집계(정책문서 17.9-A 과잉설계 방지 원칙).
        orders/positions/strategy_executions 조인, 9.4 MDD 계산 로직 재사용."""
        ...
```

---

## §16.11 FD-21 모바일 전용 — `src/api/routers/devices.py`

```python
# STATUS: SCAFFOLD-READY
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends


router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegisterRequest(BaseModel):
    device_token: str
    platform: str  # "iOS" | "Android"


class DeviceResponse(BaseModel):
    device_id: int
    registered_at: datetime
    is_active: bool


@router.post("", status_code=201)
async def register_device(
    body: DeviceRegisterRequest, user=Depends(get_current_user), db=Depends(get_db)
) -> DeviceResponse:
    """FD-21.1 — 중복(동일 user_id+device_token, is_active=True) 시 400."""
    ...


@router.delete("/{device_token}")
async def revoke_device(
    device_token: str, user=Depends(get_current_user), db=Depends(get_db)
) -> dict:
    ...
```

> FD-21.2(생체인증)는 서버 API 없음 — 기존 `POST /auth/login`으로 발급된 토큰을
> OS Keychain/Keystore에 저장하는 클라이언트(모바일 앱) 로직. 이 문서는 백엔드
> 시그니처만 다루므로 해당 사항 없음(17번 프론트엔드 문서에서 다룸).

---

## §16.12 라우터 등록 — `src/api/main.py`

```python
# STATUS: SCAFFOLD-READY
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.core.event_bus.singleton import get_event_bus
from src.api.routers import (
    auth, exchanges, marketplace, strategy_builder, suitability,
    executions, notifications, admin, portfolio, reports, devices, disputes,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """"구현자 리뷰 대조" 라운드 추가 — 05번 §5.2 EventBus 싱글톤의 start()/stop()을
    실제로 호출하는 곳이 없었음(Graceful shutdown 계약 위반 상태)."""
    await get_event_bus().start()
    yield
    await get_event_bus().stop()

app = FastAPI(title="AIOS API", version="0.1.0", lifespan=lifespan)

for router in (
    auth.router, exchanges.router, marketplace.router, strategy_builder.router,
    suitability.router, executions.router, notifications.router, admin.router,
    portfolio.router, reports.router, devices.router, disputes.router,
    # disputes.router: "0번부터 재검토" 라운드에서 등록 누락 발견해 추가
):
    app.include_router(router)

# ADR-2026-08-10-B: FastAPI 자동 OpenAPI 생성(/docs, /openapi.json)이
# 15번 API 스펙 문서를 코드 착수 시점부터 대체하기 시작한다 — 15번 문서는
# 이후 참고용, 실제 계약의 진실 원천은 여기 코드가 된다.
```

## §16.12-A 실행 명령 (신규 — "모든 문서 실제 구현가능성 검증" 라운드에서
발견: 서버 기동·DB 마이그레이션 명령이 어디에도 없어 "설치는 됐는데 어떻게
실행하지?"에 답이 없는 상태였음)

```bash
# --- 최초 1회 셋업 ---
docker compose -f docker-compose.dev.yml up -d  # 11번 §11.7, PostgreSQL 로컬 기동
pip install -e . --break-system-packages   # pyproject.toml 기준(10번 §1.1, 11번 §11.6)
cp .env.example .env                       # 07번 §7.3, 값 채워넣기
alembic init migrations                    # 최초 1회만
alembic revision --autogenerate -m "initial schema"  # 04/13번 DDL 기준 자동생성
alembic upgrade head                       # 실제 DB에 적용

# --- 개발 서버 기동 ---
uvicorn src.api.main:app --reload --port 8000
# → http://localhost:8000/docs 에서 FastAPI 자동생성 OpenAPI 문서 확인 가능
#   (§16.12 ADR-2026-08-10-B 원칙 — 15번 문서보다 이 자동생성 문서가 우선)

# --- 스키마 변경 시(신규 테이블/컬럼 추가할 때마다) ---
alembic revision --autogenerate -m "설명"
alembic upgrade head
```

- Alembic 설정(`alembic.ini`, `env.py`)은 착수 시 표준 FastAPI+SQLAlchemy
  async 템플릿을 그대로 사용 — 이 프로젝트 특유의 커스터마이징 불필요.
- CI 파이프라인(08번 §8.7)에서는 `alembic upgrade head`를 테스트 DB에 먼저
  적용한 뒤 테스트를 실행한다(Draft, 착수 시 정확한 순서 확정).

---

## §16.13 작업 트리 매핑 (10번 문서 갱신 필요)

```
├── 11.7 AuthService 구현체 (11.1~11.4 API의 서비스 계층)
├── 12.6 ExchangeCredentialService 구현체
├── 13.8 MarketplaceService 구현체
├── 14.6 ConditionCompiler/StrategyBuilderService/IndicatorService 구현체
├── 15.5 SuitabilityService/MatchingWarningService 구현체
├── 16.7 ExecutionService/RiskPolicyGate 구현체
├── 17.6 NotificationGateway/ChannelAdapter 구현체
├── 18.6 AdminService 구현체
├── 19.4 PortfolioService 구현체
├── 20.3 ReportService 구현체
├── 21.4 devices 라우터 구현체
└── 22.1 FastAPI app 조립 + 라우터 등록 (16.0~16.11 전체 이후)
```
