"""D3 깊이 증빙 for src/foundation/connections/contracts/v1.py (FA-8, FA-24, LA-1,
LB-1, LC-1) — task-10506, test_v1.py에서 분리(CLAUDE.md §12, loc_over_800 래칫).

이 모듈의 핵심 불변은 docstring에 적힌 "다른 bounded context는 contracts/v1.py만
소비하고 domain/models.py를 직접 참조하지 않는다"는 경계다. 이 경계는
scripts/check_import_linter.py의 `.importlinter` `[boundary:foundation-aggregates]`
계약으로 실제 CI에서 강제된다 — 아래 gate_red 테스트는 그 게이트가 위반을 실제로
적발함을 재현한다.
"""

import importlib.util
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from src.foundation.connections.contracts.v1 import (
    AccountConnectionView,
    AccountSnapshotView,
    CapabilityScope,
    ConnectionState,
    SnapshotValueView,
)

ROOT = Path(__file__).resolve().parents[5]


def _load_check_import_linter() -> ModuleType:
    """scripts/check_import_linter.py를 파일 경로로 직접 로드 — tests/unit/scripts/
    test_check_import_linter.py와 동일한 패턴(스크립트는 패키지가 아니라 CLI)."""
    path = ROOT / "scripts" / "check_import_linter.py"
    spec = importlib.util.spec_from_file_location("check_import_linter_v1_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# D3: 게이트 적색 재현 — check_import_linter.py의 boundary 계약이 이 모듈의
# "contracts/v1.py 경유만 허용" 경계 위반을 실제로 적발하는지
# ---------------------------------------------------------------------------


class TestImportBoundaryGateRed:
    """FA-8/FA-24/LA-1/LB-1/LC-1 깊이: contracts/v1.py의 핵심 불변은 다른 bounded
    context가 domain/adapters 내부로 직접 들어가지 않고 이 파일(contracts/)만
    소비한다는 것이다. 이 불변을 깨는 실제 import 그래프를 합성해, 그것을 지키는
    게이트(.importlinter의 [boundary:foundation-aggregates])가 실제로 적색이 됨을
    보인다 — 게이트 자체가 소프트 없이 빠져 있지 않다는 증빙."""

    def test_fails_on_injected_cross_context_domain_import(self, tmp_path: Path) -> None:
        """다른 foundation 애그리게잇이 connections의 domain/ 내부로 직접
        들어가면(=이 contracts 모듈을 우회) boundary 계약이 위반을 적발해야 한다."""
        cil = _load_check_import_linter()

        src = tmp_path / "src"
        for parts in (
            ("src",),
            ("src", "foundation"),
            ("src", "foundation", "connections"),
            ("src", "foundation", "connections", "domain"),
            ("src", "foundation", "mandates"),
            ("src", "foundation", "mandates", "application"),
        ):
            pkg_dir = tmp_path.joinpath(*parts)
            pkg_dir.mkdir(parents=True, exist_ok=True)
            (pkg_dir / "__init__.py").touch()

        (src / "foundation" / "connections" / "domain" / "models.py").write_text(
            "class AccountConnection:\n    pass\n", encoding="utf-8"
        )
        # 위반: mandates(다른 애그리게잇)이 connections.contracts.v1을 거치지 않고
        # connections.domain.models를 직접 임포트 — docstring이 금지하는 바로 그 경로.
        (src / "foundation" / "mandates" / "application" / "uses_connection.py").write_text(
            "from src.foundation.connections.domain.models import AccountConnection\n",
            encoding="utf-8",
        )

        graph = cil.build_graph(tmp_path)
        contract = {
            "kind": "boundary",
            "root": "src.foundation",
            "internal_suffixes": ["domain", "adapters"],
        }
        hits = cil._eval_boundary(graph, contract)

        assert hits, (
            "게이트 적색 재현 실패: boundary 계약이 connections.domain으로의 "
            "교차 컨텍스트 직접 임포트를 적발하지 못했다"
        )
        assert all("mandates" in violating_module for violating_module, _, _ in hits)
        assert all("connections" in message for _, _, message in hits)

    def test_passes_when_other_context_uses_contracts_only(self, tmp_path: Path) -> None:
        """대조군: 다른 애그리게잇이 contracts/를 통해서만 접근하면 위반이 없어야
        한다 — gate_red 테스트가 항상 적색을 반환하는 깨진 단언이 아님을 보장."""
        cil = _load_check_import_linter()

        for parts in (
            ("src",),
            ("src", "foundation"),
            ("src", "foundation", "connections"),
            ("src", "foundation", "connections", "contracts"),
            ("src", "foundation", "mandates"),
            ("src", "foundation", "mandates", "application"),
        ):
            pkg_dir = tmp_path.joinpath(*parts)
            pkg_dir.mkdir(parents=True, exist_ok=True)
            (pkg_dir / "__init__.py").touch()

        (tmp_path / "src" / "foundation" / "connections" / "contracts" / "v1.py").write_text(
            "class AccountConnectionView:\n    pass\n", encoding="utf-8"
        )
        (
            tmp_path / "src" / "foundation" / "mandates" / "application" / "uses_connection.py"
        ).write_text(
            "from src.foundation.connections.contracts.v1 import AccountConnectionView\n",
            encoding="utf-8",
        )

        graph = cil.build_graph(tmp_path)
        contract = {
            "kind": "boundary",
            "root": "src.foundation",
            "internal_suffixes": ["domain", "adapters"],
        }
        hits = cil._eval_boundary(graph, contract)

        assert hits == []

    def test_real_repo_connections_contract_has_no_boundary_violation(self) -> None:
        """실제 저장소: 지금 이 순간 connections 애그리게잇에 교차 컨텍스트
        domain/adapters 직접 침투가 없어야 한다(회귀 감시) — N/A가 아니라 현재
        상태가 실제로 녹색임을 같은 평가 함수로 고정한다."""
        cil = _load_check_import_linter()
        contracts = cil.parse_contracts(ROOT / ".importlinter")
        boundary_contracts = [c for c in contracts if c["kind"] == "boundary"]
        assert boundary_contracts, "boundary 계약이 .importlinter에서 사라졌다"

        graph = cil.build_graph(ROOT)
        connections_hits = [
            hit
            for contract in boundary_contracts
            for hit in cil._eval_boundary(graph, contract)
            if "connections" in hit[0] or "connections" in hit[2]
        ]
        assert connections_hits == []


# ---------------------------------------------------------------------------
# D3: 성능 단언 — perf_budget 픽스처(raw 타이머 금지, perf-measurement 래칫)
# ---------------------------------------------------------------------------


class TestContractValidationPerf:
    """contracts/v1.py의 pydantic 모델은 L4-02(연결 조회)·L4-03(스냅샷 조회) 응답
    경로의 매 호출마다 역직렬화/검증을 거친다. 검증 자체가 병목이 되면 안 된다는
    성능 하한을 perf_budget으로 고정한다(raw time.perf_counter 직접 비교 금지 —
    perf-measurement 래칫)."""

    @pytest.mark.perf
    def test_account_connection_view_construction_latency(self, perf_budget) -> None:
        iterations = 2_000
        conn_id = uuid4()
        now = datetime.now(timezone.utc)

        def _run() -> None:
            for _ in range(iterations):
                AccountConnectionView(
                    id=conn_id,
                    provider_code="BINANCE",
                    masked_account_label="****1234",
                    state=ConnectionState.ACTIVE_READONLY,
                    capability_profile=[CapabilityScope.READ_BALANCE],
                    revision=1,
                    created_at=now,
                )

        perf_budget.assert_within(
            _run,
            budget_ms=500.0,
            label="AccountConnectionView 생성 2000회 — 예산 0.5초",
        )

    @pytest.mark.perf
    def test_account_snapshot_view_with_values_construction_latency(self, perf_budget) -> None:
        iterations = 1_000
        conn_id = uuid4()
        now = datetime.now(timezone.utc)
        values = [
            SnapshotValueView(entity_type="ASSET", entity_key=f"TOK{i}", value=Decimal("1.5"))
            for i in range(10)
        ]

        def _run() -> None:
            for _ in range(iterations):
                AccountSnapshotView(
                    connection_id=conn_id,
                    captured_at=now,
                    provider_as_of=now,
                    freshness="1m",
                    currency="USD",
                    values=values,
                )

        perf_budget.assert_within(
            _run,
            budget_ms=500.0,
            label="AccountSnapshotView(values=10) 생성 1000회 — 예산 0.5초",
        )
