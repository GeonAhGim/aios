"""tests/foundation/integration/screener/__init__.py — screener 패키지
application 계층(save/share/alert) 부정/실패주입/성능 테스트 (fake ports, no DB).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 — negative>=3, failure-injection>=1, perf assertion>=1

이 디렉터리의 다른 파일(test_postgres_*.py, test_save_share_alert.py)은 실DB
(TEST_DATABASE_URL) 경로를 검증한다. `tests/foundation/screener/test_{query_plan,
evaluate,run_screen}.py`는 domain/query_plan.py, domain/evaluate.py,
application/run_screen.py를 fake port로 이미 두껍게 덮는다(교체 삭제된
test_{save_screen,share_screen,alert_on_screen}.py의 빈자리) — 남은 공백은
application/{save_screen,share_screen,alert_on_screen}.py 자체의 실DB 없는
경로 검증이다. 이 파일은 그 공백을 메운다. fakes는 `_fakes.py`로 분리했고
(CLAUDE.md §9 파일 정책 — fakes/helpers는 그것을 쓰는 테스트와 다른 책임),
cross-module 통합 테스트는 `test_application_fakes_integration.py`에 있다.

불변식 참조:
- `save_screen.save_screen`: `build_query_plan`/`validate_plan_supported`이
  통과하지 못하는 `ScreenDefinition`은 저장소에 절대 도달하지 않는다
  (컴파일 실패 후 un-runnable 상태로 저장되는 경우가 없어야 함).
- `share_screen.share_screen`/`alert_on_screen.create_screen_alert`: cross-tenant
  접근은 그 존재 자체를 드러내지 않고 `None`을 반환한다(§71 §4 컨벤션).
- `alert_on_screen.evaluate_screen_alerts`: 한 alert의 화면 실행 오류
  (`_SCREEN_EXECUTION_ERRORS`에 나열된 것만)는 그 alert만 건너뛰고 나머지
  평가 사이클은 계속된다 — 나열되지 않은 예외(의존성 크래시 등)는 여전히
  전파되어 사이클 전체를 중단시킨다(그 경계를 아래 실패주입 테스트가 증명한다).
"""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.screener.application.alert_on_screen import (
    create_screen_alert,
    evaluate_screen_alerts,
)
from src.foundation.screener.application.run_screen import ScreenResultCache
from src.foundation.screener.application.save_screen import save_screen
from src.foundation.screener.application.share_screen import share_screen
from src.foundation.screener.contracts.v1 import (
    MAX_CONDITION_SOURCE_LENGTH,
    IndicatorFilter,
    ResearchFilter,
    ScreenDefinition,
)
from src.foundation.screener.domain.query_plan import ScreenerConditionError
from src.foundation.screener.ports.repository import SavedScreenerLimitError
from tests.foundation.integration.screener._fakes import (
    FakeFieldSource,
    FakeSavedScreenerRepository,
    FakeScreenAlertRepository,
    FakeSharedScreenerRepository,
)
from tests.foundation.integration.screener._fakes import definition as _definition
from tests.foundation.integration.screener._fakes import instrument as _instrument

# ──────────────────────────────────────────────────────────────────────
# 1. Negative tests — 불변식 위반 입력을 명시적으로 거부
# ──────────────────────────────────────────────────────────────────────


class TestNegativeContractConstruction:
    """contracts/v1.py 생성 시점 부정 테스트 — domain 컴파일 이전, pydantic 레벨."""

    def test_negative_research_filter_rejects_naive_as_of(self) -> None:
        """부정: tz-naive as_of는 ResearchFilter 생성 자체가 거부됨(RD-A1)."""
        with pytest.raises(ValidationError):
            ResearchFilter(condition="eps_surprise > 0", as_of=datetime(2026, 1, 1))

    def test_negative_condition_exceeds_max_length_rejected(self) -> None:
        """부정: condition이 MAX_CONDITION_SOURCE_LENGTH를 초과하면 생성이 거부됨."""
        too_long = "a" * (MAX_CONDITION_SOURCE_LENGTH + 1)
        with pytest.raises(ValidationError):
            IndicatorFilter(condition=too_long)

    def test_negative_blank_condition_rejected(self) -> None:
        """부정: 공백만 있는 condition은 strip 후 빈 문자열이 되어 거부됨."""
        with pytest.raises(ValidationError):
            IndicatorFilter(condition="   ")

    def test_negative_screen_definition_rejects_blank_universe(self) -> None:
        """부정: universe가 공백뿐이면 ScreenDefinition 생성이 거부됨."""
        with pytest.raises(ValidationError):
            ScreenDefinition(universe="   ", filters=(IndicatorFilter(condition="close > 1"),))


class TestNegativeSaveScreenFailsClosedBeforePersist:
    """save_screen.py — 컴파일 실패 시 저장소에 절대 도달하지 않는다."""

    async def test_negative_uncompilable_definition_never_reaches_repo(self) -> None:
        """부정: 구문 오류가 있는 조건은 repo.save 호출 전에 ScreenerConditionError로
        거부되며, 저장소는 단 한 번도 호출되지 않는다."""
        repo = FakeSavedScreenerRepository()
        bad = _definition(condition="close >")

        with pytest.raises(ScreenerConditionError) as exc_info:
            await save_screen(uuid4(), name="broken", definition=bad, repo=repo)

        assert exc_info.value.code == "SCREENER_CONDITION_SYNTAX"
        assert repo.save_calls == 0

    async def test_negative_share_cross_tenant_returns_none_without_touching_shared_repo(
        self,
    ) -> None:
        """부정: 다른 테넌트 소유 screener를 공유 시도하면 None(404 대응)이며,
        shared_repo.create_version은 호출되지 않는다(존재 자체를 드러내지 않음)."""
        saved_repo = FakeSavedScreenerRepository()
        shared_repo = FakeSharedScreenerRepository()
        owner, stranger = uuid4(), uuid4()
        saved = await saved_repo.save(tenant_id=owner, name="mine", definition=_definition())

        result = await share_screen(
            stranger, saved.id, saved_repo=saved_repo, shared_repo=shared_repo
        )

        assert result is None
        assert shared_repo.create_version_calls == 0

    async def test_negative_create_alert_cross_tenant_returns_none_without_touching_alert_repo(
        self,
    ) -> None:
        """부정: 다른 테넌트 소유 screener에 alert 생성을 시도하면 None이며,
        alert_repo.create는 호출되지 않는다."""
        saved_repo = FakeSavedScreenerRepository()
        alert_repo = FakeScreenAlertRepository()
        owner, stranger = uuid4(), uuid4()
        saved = await saved_repo.save(tenant_id=owner, name="mine", definition=_definition())

        result = await create_screen_alert(
            stranger,
            saved.id,
            operator="gte",
            threshold=1,
            saved_repo=saved_repo,
            alert_repo=alert_repo,
        )

        assert result is None
        assert alert_repo.create_calls == 0


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection tests — 의존성/경계 예외 유발
# ──────────────────────────────────────────────────────────────────────


class TestFailureInjectionSaveScreen:
    def test_failure_injection_repo_limit_error_propagates(self) -> None:
        """실패주입: 저장소가 SavedScreenerLimitError(캡 초과)를 던지면
        save_screen은 이를 삼키지 않고 그대로 전파한다(fail-closed)."""
        import asyncio

        repo = FakeSavedScreenerRepository(raise_on_save=SavedScreenerLimitError("cap hit"))

        async def _run() -> None:
            with pytest.raises(SavedScreenerLimitError):
                await save_screen(uuid4(), name="x", definition=_definition(), repo=repo)

        asyncio.run(_run())
        assert repo.save_calls == 1


class TestFailureInjectionEvaluateScreenAlerts:
    """alert_on_screen.evaluate_screen_alerts — 한 alert의 실행 오류만 건너뛰는지,
    그리고 그 경계 밖(임의 의존성 크래시)은 여전히 전파되는지 둘 다 증명한다."""

    async def test_failure_injection_one_alert_screen_error_skips_only_that_alert(
        self,
    ) -> None:
        """실패주입: 한 alert의 화면이 ScreenerEvaluationError(알 수 없는 필드)로
        실패해도, 나머지 alert 평가는 계속 진행된다 (fail-open-per-item)."""
        saved_repo = FakeSavedScreenerRepository()
        alert_repo = FakeScreenAlertRepository()
        tenant = uuid4()

        broken_saved = await saved_repo.save(
            tenant_id=tenant, name="broken", definition=_definition(condition="per < 15")
        )
        healthy_saved = await saved_repo.save(
            tenant_id=tenant, name="healthy", definition=_definition(condition="close > 100")
        )
        broken_alert = await alert_repo.create(
            tenant_id=tenant, screener_id=broken_saved.id, operator="gte", threshold=1
        )
        healthy_alert = await alert_repo.create(
            tenant_id=tenant, screener_id=healthy_saved.id, operator="gte", threshold=1
        )

        a = uuid4()
        field_source = FakeFieldSource(
            [_instrument(a, symbol="AAA")], {a: {"close": Decimal("200")}}
        )

        triggers = await evaluate_screen_alerts(
            alert_repo=alert_repo,
            saved_repo=saved_repo,
            field_source=field_source,
            cache=ScreenResultCache(),
        )

        assert [t.alert.id for t in triggers] == [healthy_alert.id]
        # broken alert was skipped, not raised -- it stays ACTIVE (not TRIGGERED)
        still_active = await alert_repo.get(tenant, broken_alert.id)
        assert still_active is not None
        assert still_active.status == "ACTIVE"

    async def test_failure_injection_dependency_crash_propagates_past_fail_open_boundary(
        self,
    ) -> None:
        """실패주입: field_source.read_fields가 (화면 실행 오류가 아닌) 임의의
        RuntimeError로 크래시하면, evaluate_screen_alerts는 이를 삼키지 않고
        전체 평가 사이클을 중단시킨다 -- fail-open은 `_SCREEN_EXECUTION_ERRORS`로
        나열된 화면 실행 오류에만 적용되고, 임의의 의존성 장애까지 감추지 않는다
        (CLAUDE.md §3 fail-closed 기본 자세)."""
        saved_repo = FakeSavedScreenerRepository()
        alert_repo = FakeScreenAlertRepository()
        tenant = uuid4()
        saved = await saved_repo.save(tenant_id=tenant, name="x", definition=_definition())
        await alert_repo.create(tenant_id=tenant, screener_id=saved.id, operator="gte", threshold=1)
        a = uuid4()
        field_source = FakeFieldSource(
            [_instrument(a, symbol="AAA")],
            {a: {"close": Decimal("200")}},
            raise_on_read=RuntimeError("storage outage"),
        )

        with pytest.raises(RuntimeError, match="storage outage"):
            await evaluate_screen_alerts(
                alert_repo=alert_repo,
                saved_repo=saved_repo,
                field_source=field_source,
                cache=ScreenResultCache(),
            )


# ──────────────────────────────────────────────────────────────────────
# 3. Performance assertion
# ──────────────────────────────────────────────────────────────────────


class TestPerformanceEvaluateScreenAlerts:
    @pytest.mark.perf
    async def test_perf_evaluate_many_active_alerts_within_budget(self) -> None:
        """성능단언: 알림 평가 배치 처리 -- ADR-2026-09-09-C 표에 별도 항목이
        없는 UX 경량 경로 축이므로, `run_screen.py`가 이미 쓰는 "5k 심볼 2초"
        보다 훨씬 느슨한 예산(alert 50건 x 1심볼 스캔이 3초 미만)으로 회귀를
        잡는다."""
        saved_repo = FakeSavedScreenerRepository()
        alert_repo = FakeScreenAlertRepository()
        tenant = uuid4()
        a = uuid4()
        field_source = FakeFieldSource(
            [_instrument(a, symbol="AAA")], {a: {"close": Decimal("200")}}
        )

        for i in range(50):
            saved = await saved_repo.save(tenant_id=tenant, name=f"s{i}", definition=_definition())
            await alert_repo.create(
                tenant_id=tenant, screener_id=saved.id, operator="gte", threshold=1
            )

        start = time.perf_counter()
        triggers = await evaluate_screen_alerts(
            alert_repo=alert_repo,
            saved_repo=saved_repo,
            field_source=field_source,
            cache=ScreenResultCache(),
        )
        elapsed = time.perf_counter() - start

        assert len(triggers) == 50
        assert elapsed < 3.0, f"alert 50건 평가에 {elapsed:.3f}s -- 예산 3.0s 초과"
