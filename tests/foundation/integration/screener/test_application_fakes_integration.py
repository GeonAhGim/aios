"""screener application 계층 cross-module 통합 테스트 (fake ports, no DB).

`__init__.py`의 negative/failure-injection/perf 테스트와 같은 DEEPEN(task-6704
고아 산출물 회수 5828 (qa-2))에서 분리된 파일 — CLAUDE.md §9 파일 정책
(loc_over_500 근처에서는 책임별로 분할) 및 §6 #12(근-임계 파일에 D2/D3 증빙을
더 쌓기 전에 분할)에 따라, fakes(`_fakes.py`)를 공유하는 cross-module
통합 테스트를 여기로 옮겼다.

save_screen -> share_screen (MP-3 immutable-version), save_screen ->
create_screen_alert -> evaluate_screen_alerts (트리거 idempotency, 캡이
평가가 아닌 생성 시점에만 강제됨) 두 전체 사이클을 검증한다.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.screener.application.alert_on_screen import (
    create_screen_alert,
    evaluate_screen_alerts,
)
from src.foundation.screener.application.run_screen import ScreenResultCache
from src.foundation.screener.application.save_screen import save_screen
from src.foundation.screener.application.share_screen import share_screen
from src.foundation.screener.ports.repository import ScreenAlertLimitError
from tests.foundation.integration.screener._fakes import (
    FakeFieldSource,
    FakeSavedScreenerRepository,
    FakeScreenAlertRepository,
    FakeSharedScreenerRepository,
)
from tests.foundation.integration.screener._fakes import definition as _definition
from tests.foundation.integration.screener._fakes import instrument as _instrument


class TestIntegrationSaveShareRoundTrip:
    """save_screen.py + share_screen.py — 저장 후 공유, MP-3 immutable-version."""

    async def test_integration_save_then_share_creates_version_one(self) -> None:
        """통합: 저장한 screener를 처음 공유하면 version=1이 생성된다."""
        saved_repo = FakeSavedScreenerRepository()
        shared_repo = FakeSharedScreenerRepository()
        tenant = uuid4()
        saved = await save_screen(
            tenant, name="my-screen", definition=_definition(), repo=saved_repo
        )

        shared = await share_screen(
            tenant, saved.id, saved_repo=saved_repo, shared_repo=shared_repo
        )

        assert shared is not None
        assert shared.version == 1

    async def test_integration_sharing_again_appends_a_new_version_not_a_mutation(
        self,
    ) -> None:
        """통합: 같은 screener를 다시 공유하면 기존 version을 덮어쓰지 않고
        다음 version을 append한다 -- 과거 버전은 그대로 조회 가능해야 한다."""
        saved_repo = FakeSavedScreenerRepository()
        shared_repo = FakeSharedScreenerRepository()
        tenant = uuid4()
        saved = await save_screen(
            tenant, name="my-screen", definition=_definition(), repo=saved_repo
        )

        first = await share_screen(tenant, saved.id, saved_repo=saved_repo, shared_repo=shared_repo)
        second = await share_screen(
            tenant, saved.id, saved_repo=saved_repo, shared_repo=shared_repo
        )

        assert first is not None
        assert second is not None
        assert (first.version, second.version) == (1, 2)
        versions = await shared_repo.list_versions(saved.id)
        assert [v.version for v in versions] == [1, 2]
        assert versions[0].definition == versions[1].definition


class TestIntegrationAlertLifecycle:
    """save_screen.py + alert_on_screen.py — 생성 -> 평가 -> 트리거 -> 재평가
    (idempotent) 전체 사이클."""

    async def test_integration_create_evaluate_trigger_then_reevaluate_is_idempotent(
        self,
    ) -> None:
        """통합: alert이 트리거된 후 같은 조건으로 다시 평가해도 TRIGGERED->ACTIVE
        복귀나 중복 트리거 없이 그대로 유지된다 (mark_triggered의 idempotent 계약)."""
        saved_repo = FakeSavedScreenerRepository()
        alert_repo = FakeScreenAlertRepository()
        tenant = uuid4()
        saved = await save_screen(
            tenant, name="my-screen", definition=_definition(), repo=saved_repo
        )
        alert = await create_screen_alert(
            tenant,
            saved.id,
            operator="gte",
            threshold=1,
            saved_repo=saved_repo,
            alert_repo=alert_repo,
        )
        assert alert is not None
        a = uuid4()
        field_source = FakeFieldSource(
            [_instrument(a, symbol="AAA")], {a: {"close": Decimal("200")}}
        )
        cache = ScreenResultCache()

        first_pass = await evaluate_screen_alerts(
            alert_repo=alert_repo, saved_repo=saved_repo, field_source=field_source, cache=cache
        )
        second_pass = await evaluate_screen_alerts(
            alert_repo=alert_repo, saved_repo=saved_repo, field_source=field_source, cache=cache
        )

        assert len(first_pass) == 1
        assert first_pass[0].alert.status == "TRIGGERED"
        # already TRIGGERED -- list_active no longer returns it, so the second
        # pass sees zero active alerts and produces zero new triggers
        assert second_pass == []

    async def test_integration_alert_cap_enforced_only_at_creation_not_evaluation(
        self,
    ) -> None:
        """통합: MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT 캡은 create_screen_alert
        시점에만 강제되며, 이미 생성된 alert의 평가 자체는 캡과 무관하게 진행된다."""
        saved_repo = FakeSavedScreenerRepository()
        alert_repo = FakeScreenAlertRepository(limit=1)
        tenant = uuid4()
        saved = await save_screen(
            tenant, name="only-one", definition=_definition(), repo=saved_repo
        )
        first = await create_screen_alert(
            tenant,
            saved.id,
            operator="gte",
            threshold=1,
            saved_repo=saved_repo,
            alert_repo=alert_repo,
        )
        assert first is not None

        with pytest.raises(ScreenAlertLimitError):
            await create_screen_alert(
                tenant,
                saved.id,
                operator="gte",
                threshold=1,
                saved_repo=saved_repo,
                alert_repo=alert_repo,
            )

        # the already-created alert still evaluates fine despite the tenant
        # being at (in fact over, if the second create had not raised) cap
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
        assert len(triggers) == 1
