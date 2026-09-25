"""UX-7 `save_screen`/`share_screen`/`alert_on_screen` integration tests
(real DB, TEST_DATABASE_URL) + migration (2e97e28fe878) gate-red repro.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §9 UX-7 DoD
("저장·공유·조건 알림"), ADR-2026-09-09-C Decision 1 (per-axis performance
budgets, "스크리너 5k 심볼 2초" — reused here since `evaluate_screen_alerts`'s
dominant cost is the same universe scan `run_screen.py` already budgets),
ADR-2026-09-09-C Decision 4 (D2 floor: negative >=3, failure injection 1,
perf assertion 1, gate-red repro 1). depth=D2 (UX axis, not R/L4/LA/LB/LC/
FA/CM/EO/DC, so D3 does not apply).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import InstrumentRef, SymbolStatus, Venue
from src.foundation.screener.adapters.postgres_repository import (
    PostgresSavedScreenerRepository,
    PostgresScreenAlertRepository,
    PostgresSharedScreenerRepository,
)
from src.foundation.screener.application.alert_on_screen import (
    cancel_screen_alert,
    create_screen_alert,
    evaluate_screen_alerts,
    list_screen_alerts,
)
from src.foundation.screener.application.run_screen import ScreenResultCache
from src.foundation.screener.application.save_screen import save_screen
from src.foundation.screener.application.share_screen import share_screen
from src.foundation.screener.contracts.v1 import IndicatorFilter, ScreenDefinition
from src.foundation.screener.domain.query_plan import ScreenerConditionError
from src.foundation.screener.ports.repository import ScreenAlertLimitError
from tests.integration.conftest import create_test_tenant

_LISTED_AT_DATETIME = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def saved_repo(pool: asyncpg.Pool) -> PostgresSavedScreenerRepository:
    return PostgresSavedScreenerRepository(pool)


@pytest.fixture
def shared_repo(pool: asyncpg.Pool) -> PostgresSharedScreenerRepository:
    return PostgresSharedScreenerRepository(pool)


@pytest.fixture
def alert_repo(pool: asyncpg.Pool) -> PostgresScreenAlertRepository:
    return PostgresScreenAlertRepository(pool)


def _definition(*, universe: str = "KIS_KRX") -> ScreenDefinition:
    return ScreenDefinition(
        universe=universe,
        filters=(IndicatorFilter(condition="close > 0"),),
    )


def _instrument(instrument_id: UUID, *, symbol: str) -> InstrumentRef:
    return InstrumentRef(
        instrument_id=instrument_id,
        venue=Venue.KIS_KRX,
        canonical_symbol=symbol,
        venue_symbol=symbol,
        asset_class=AssetClass.KR_EQUITY,
        base=None,
        quote=None,
        tick_size=Decimal("1"),
        lot_size=Decimal("1"),
        status=SymbolStatus.LISTED,
        listed_at=_LISTED_AT_DATETIME,
        delisted_at=None,
    )


class FakeFieldSource:
    """Minimal in-memory `ScreenerFieldSource` (mirrors
    tests/foundation/screener/test_run_screen.py's fixture) — `evaluate_screen_alerts`
    only needs a match/no-match source, not the real DC-13 index."""

    def __init__(self, instruments, fields) -> None:
        self._instruments = sorted(instruments, key=lambda i: str(i.instrument_id))
        self._fields = fields

    async def universe_page(self, *, venues, after, limit):
        pool_ = [i for i in self._instruments if i.venue in venues]
        if after is not None:
            idx = next((k for k, i in enumerate(pool_) if i.instrument_id == after), len(pool_))
            pool_ = pool_[idx + 1 :]
        return pool_[:limit]

    async def read_fields(self, *, instrument_ids_by_venue, field_names, as_of):
        result = {}
        for ids in instrument_ids_by_venue.values():
            for instrument_id in ids:
                row = self._fields.get(instrument_id)
                if row is None:
                    continue
                result[instrument_id] = {k: v for k, v in row.items() if k in field_names}
        return result


# ---- save_screen: validates before persisting ----


async def test_save_screen_persists_and_is_gettable(pool, saved_repo) -> None:
    tenant_id = await create_test_tenant(pool)

    saved = await save_screen(
        tenant_id, name="my screen", definition=_definition(), repo=saved_repo
    )

    fetched = await saved_repo.get(tenant_id, saved.id)
    assert fetched is not None
    assert fetched.name == "my screen"


# ---- negative #1: save_screen rejects an uncompilable condition before it ever
# reaches storage (nothing is persisted) ----


async def test_save_screen_rejects_uncompilable_condition(pool, saved_repo) -> None:
    tenant_id = await create_test_tenant(pool)
    broken = ScreenDefinition(universe="kr_stocks", filters=(IndicatorFilter(condition="close +"),))

    with pytest.raises(ScreenerConditionError):
        await save_screen(tenant_id, name="broken", definition=broken, repo=saved_repo)

    assert await saved_repo.list_for_tenant(tenant_id) == ()


# ---- share_screen: MP-3 immutable-version rule applied locally ----


async def test_share_screen_creates_version_one(pool, saved_repo, shared_repo) -> None:
    tenant_id = await create_test_tenant(pool)
    saved = await save_screen(tenant_id, name="s1", definition=_definition(), repo=saved_repo)

    shared = await share_screen(tenant_id, saved.id, saved_repo=saved_repo, shared_repo=shared_repo)

    assert shared is not None
    assert shared.version == 1
    assert shared.screener_id == saved.id


async def test_share_screen_twice_creates_immutable_version_two(
    pool, saved_repo, shared_repo
) -> None:
    tenant_id = await create_test_tenant(pool)
    saved = await save_screen(tenant_id, name="s1", definition=_definition(), repo=saved_repo)

    first = await share_screen(tenant_id, saved.id, saved_repo=saved_repo, shared_repo=shared_repo)
    second = await share_screen(tenant_id, saved.id, saved_repo=saved_repo, shared_repo=shared_repo)

    assert first is not None and second is not None
    assert first.version == 1
    assert second.version == 2
    versions = await shared_repo.list_versions(saved.id)
    assert [v.version for v in versions] == [1, 2]
    # immutability: the first shared row's definition never changes underneath it
    assert versions[0].definition == first.definition


# ---- negative #2: sharing another tenant's saved screener is a 404, not a leak ----


async def test_share_screen_cross_tenant_returns_none(pool, saved_repo, shared_repo) -> None:
    owner = await create_test_tenant(pool)
    stranger = await create_test_tenant(pool)
    saved = await save_screen(owner, name="mine", definition=_definition(), repo=saved_repo)

    result = await share_screen(stranger, saved.id, saved_repo=saved_repo, shared_repo=shared_repo)

    assert result is None
    assert await shared_repo.get_latest(saved.id) is None


# ---- alert_on_screen: create/list/cancel ----


async def test_create_screen_alert_then_list(pool, saved_repo, alert_repo) -> None:
    tenant_id = await create_test_tenant(pool)
    saved = await save_screen(tenant_id, name="s1", definition=_definition(), repo=saved_repo)

    alert = await create_screen_alert(
        tenant_id,
        saved.id,
        operator="gte",
        threshold=1,
        saved_repo=saved_repo,
        alert_repo=alert_repo,
    )

    assert alert is not None
    assert alert.status == "ACTIVE"
    alerts = await list_screen_alerts(tenant_id, alert_repo=alert_repo)
    assert [a.id for a in alerts] == [alert.id]


# ---- negative #3: creating an alert on another tenant's screener is a 404 ----


async def test_create_screen_alert_cross_tenant_returns_none(pool, saved_repo, alert_repo) -> None:
    owner = await create_test_tenant(pool)
    stranger = await create_test_tenant(pool)
    saved = await save_screen(owner, name="mine", definition=_definition(), repo=saved_repo)

    result = await create_screen_alert(
        stranger,
        saved.id,
        operator="gte",
        threshold=1,
        saved_repo=saved_repo,
        alert_repo=alert_repo,
    )

    assert result is None
    assert await alert_repo.list_for_tenant(stranger) == ()


# ---- negative #4: cancelling another tenant's alert is a no-op, not a 500 ----


async def test_cancel_screen_alert_cross_tenant_returns_false(pool, saved_repo, alert_repo) -> None:
    owner = await create_test_tenant(pool)
    stranger = await create_test_tenant(pool)
    saved = await save_screen(owner, name="mine", definition=_definition(), repo=saved_repo)
    alert = await create_screen_alert(
        owner, saved.id, operator="gte", threshold=1, saved_repo=saved_repo, alert_repo=alert_repo
    )
    assert alert is not None

    cancelled = await cancel_screen_alert(stranger, alert.id, alert_repo=alert_repo)

    assert cancelled is False
    still_active = await alert_repo.get(owner, alert.id)
    assert still_active is not None
    assert still_active.status == "ACTIVE"


# ---- negative #5: per-tenant ACTIVE screen-alert cap is enforced ----


async def test_create_screen_alert_active_cap_is_enforced(pool, saved_repo, alert_repo) -> None:
    tenant_id = await create_test_tenant(pool)
    saved = await save_screen(tenant_id, name="s1", definition=_definition(), repo=saved_repo)
    from src.foundation.screener.contracts.v1 import MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT

    for _ in range(MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT):
        result = await create_screen_alert(
            tenant_id,
            saved.id,
            operator="gte",
            threshold=1,
            saved_repo=saved_repo,
            alert_repo=alert_repo,
        )
        assert result is not None

    with pytest.raises(ScreenAlertLimitError):
        await create_screen_alert(
            tenant_id,
            saved.id,
            operator="gte",
            threshold=1,
            saved_repo=saved_repo,
            alert_repo=alert_repo,
        )


async def _clear_active_alerts(alert_repo) -> None:
    """`screen_alerts` deliberately carries no RLS (migration docstring) —
    `list_active()` is a genuine cross-tenant read, same as
    `AlertService.evaluate_all_active`. That means it also picks up ACTIVE
    rows left behind by earlier tests sharing this DB (e.g. the cap-enforced
    test's 50 rows) — clear the slate before exercising `evaluate_screen_alerts`
    so a test only ever sees the alert(s) it just created."""
    for alert in await alert_repo.list_active():
        await alert_repo.cancel(alert.tenant_id, alert.id)


# ---- evaluate_screen_alerts: triggers, marks TRIGGERED (no longer ACTIVE
# afterward, so a repeat evaluation cycle does not re-trigger it) ----


async def test_evaluate_screen_alerts_triggers_and_is_not_reevaluated(
    pool, saved_repo, alert_repo
) -> None:
    await _clear_active_alerts(alert_repo)
    tenant_id = await create_test_tenant(pool)
    saved = await save_screen(tenant_id, name="s1", definition=_definition(), repo=saved_repo)
    alert = await create_screen_alert(
        tenant_id,
        saved.id,
        operator="gte",
        threshold=1,
        saved_repo=saved_repo,
        alert_repo=alert_repo,
    )
    assert alert is not None
    instruments = [_instrument(uuid4(), symbol="AAA")]
    fields = {instruments[0].instrument_id: {"close": Decimal("1")}}
    field_source = FakeFieldSource(instruments, fields)

    triggers = await evaluate_screen_alerts(
        alert_repo=alert_repo,
        saved_repo=saved_repo,
        field_source=field_source,
        cache=ScreenResultCache(),
    )

    assert [t.alert.id for t in triggers] == [alert.id]
    assert triggers[0].matched_count == 1
    triggered_row = await alert_repo.get(tenant_id, alert.id)
    assert triggered_row is not None
    assert triggered_row.status == "TRIGGERED"

    # a second evaluation cycle no longer sees it (status != ACTIVE)
    second_pass = await evaluate_screen_alerts(
        alert_repo=alert_repo,
        saved_repo=saved_repo,
        field_source=field_source,
        cache=ScreenResultCache(),
    )
    assert second_pass == []


# ---- failure injection: one alert's screen fails to execute (unknown venue in
# `universe`) but the evaluation cycle still evaluates and triggers the next
# alert instead of aborting ----


async def test_evaluate_screen_alerts_skips_failing_alert_and_continues(
    pool, saved_repo, alert_repo
) -> None:
    await _clear_active_alerts(alert_repo)
    tenant_id = await create_test_tenant(pool)
    broken_screen = await save_screen(
        tenant_id,
        name="broken-universe",
        definition=_definition(universe="NOT_A_REAL_VENUE"),
        repo=saved_repo,
    )
    healthy_screen = await save_screen(
        tenant_id, name="healthy", definition=_definition(), repo=saved_repo
    )
    broken_alert = await create_screen_alert(
        tenant_id,
        broken_screen.id,
        operator="gte",
        threshold=1,
        saved_repo=saved_repo,
        alert_repo=alert_repo,
    )
    healthy_alert = await create_screen_alert(
        tenant_id,
        healthy_screen.id,
        operator="gte",
        threshold=1,
        saved_repo=saved_repo,
        alert_repo=alert_repo,
    )
    assert broken_alert is not None and healthy_alert is not None
    instruments = [_instrument(uuid4(), symbol="AAA")]
    fields = {instruments[0].instrument_id: {"close": Decimal("1")}}
    field_source = FakeFieldSource(instruments, fields)

    triggers = await evaluate_screen_alerts(
        alert_repo=alert_repo,
        saved_repo=saved_repo,
        field_source=field_source,
        cache=ScreenResultCache(),
    )

    assert [t.alert.id for t in triggers] == [healthy_alert.id]
    broken_row = await alert_repo.get(tenant_id, broken_alert.id)
    assert broken_row is not None
    assert broken_row.status == "ACTIVE"  # untouched, not crashed the whole cycle


# ---- perf assertion: ADR-2026-09-09-C 축별 예산 "스크리너 5k 심볼 2초" — the
# dominant cost inside evaluate_screen_alerts is the same universe scan
# run_screen.py already budgets ----


@pytest.mark.perf
async def test_evaluate_screen_alerts_5000_symbol_scan_within_adr_budget(
    pool, saved_repo, alert_repo
) -> None:
    await _clear_active_alerts(alert_repo)
    tenant_id = await create_test_tenant(pool)
    saved = await save_screen(tenant_id, name="big", definition=_definition(), repo=saved_repo)
    # No row matches ("close" always 0, condition is "close > 0"), and the
    # alert triggers on the "no match" case (operator="eq", threshold=0) —
    # so the scan must walk the full 5,000-symbol universe end to end
    # instead of stopping early at the MAX_RESULT_ROWS cap (same rationale
    # as test_run_screen.py's identical perf test).
    alert = await create_screen_alert(
        tenant_id,
        saved.id,
        operator="eq",
        threshold=0,
        saved_repo=saved_repo,
        alert_repo=alert_repo,
    )
    assert alert is not None
    instruments = [_instrument(uuid4(), symbol=f"S{i:05d}") for i in range(5000)]
    fields = {i.instrument_id: {"close": Decimal("0")} for i in instruments}
    field_source = FakeFieldSource(instruments, fields)

    start = time.perf_counter()
    triggers = await evaluate_screen_alerts(
        alert_repo=alert_repo,
        saved_repo=saved_repo,
        field_source=field_source,
        cache=ScreenResultCache(),
    )
    elapsed = time.perf_counter() - start

    assert len(triggers) == 1
    assert triggers[0].matched_count == 0
    assert elapsed < 2.0  # ADR-2026-09-09-C: "스크리너 5k 심볼 2초"


# ---- gate-red repro: prove `check_migration_chain.py` actually catches a
# duplicate head at this leaf's parent (7a2523bc3e5a), then prove the real
# versions dir is clean at that same parent ----

_REAL_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "src" / "db" / "migrations" / "versions"


def _write_revision(directory: Path, *, filename: str, revision: str, down_revision: str) -> None:
    (directory / filename).write_text(
        f'revision: str = "{revision}"\ndown_revision: str | None = "{down_revision}"\n',
        encoding="utf-8",
    )


def test_real_versions_dir_has_no_chain_issues_at_ux7_migration() -> None:
    from scripts.check_migration_chain import find_chain_issues

    assert find_chain_issues(_REAL_VERSIONS_DIR) == []


def test_gate_catches_duplicate_head_at_ux7_parent(tmp_path: Path) -> None:
    from scripts.check_migration_chain import find_chain_issues

    _write_revision(
        tmp_path,
        filename="2e97e28fe878_ux7_shared_screeners_screen_alerts.py",
        revision="2e97e28fe878",
        down_revision="7a2523bc3e5a",
    )
    _write_revision(
        tmp_path,
        filename="aaaaaaaaaaaa_conflicting_branch.py",
        revision="aaaaaaaaaaaa",
        down_revision="7a2523bc3e5a",
    )

    issues = find_chain_issues(tmp_path)

    assert any("다중 head" in issue for issue in issues)
