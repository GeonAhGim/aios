"""L4-30b — 리포트 생성 (docs/ops/CANARY_<date>.md).

Spec: task-2750. `scripts/canary_bitget.py`에서 분리된 책임 단위 — 세션
결과를 사람이 읽는 마크다운 리포트로 렌더링/기록하는 부분만 담당한다
(RATCHET-split task-10863, ADR-2026-09-10-C LOC 규율).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scripts.canary_bitget_lib.gate import GateDecision
from scripts.canary_bitget_lib.roundtrip import LimitRoundtripResult, MarketRoundtripResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "ops"


@dataclass(frozen=True)
class CanaryReport:
    run_date: str
    account_mode: str
    rejected_cases: tuple[GateDecision, ...]
    limit_roundtrip: LimitRoundtripResult | None = None
    market_roundtrip: MarketRoundtripResult | None = None
    notes: str = ""


def render_report(report: CanaryReport) -> str:
    lines = [
        f"# Bitget 실계좌 카나리아 리포트 — {report.run_date}",
        "",
        f"- 계정 모드: `{report.account_mode}`",
        "",
        "## 1. 거부 케이스 (로컬 게이트 REJECT, 거래소로 미전송)",
        "",
    ]
    for decision in report.rejected_cases:
        lines.append(f"- `{decision.verdict.value}` — {decision.reason}")
    lines.append("")

    lines.append("## 2. 지정가(시장가 대비 30% 이격) place/get/cancel 왕복")
    lines.append("")
    if report.limit_roundtrip is None:
        lines.append("- 실행되지 않음")
    else:
        r = report.limit_roundtrip
        lines.extend(
            [
                f"- 주문 id: `{r.exchange_order_id}`",
                f"- place 직후 상태: `{r.placed_status}`",
                f"- get 상태: `{r.fetched_status}`",
                f"- cancel 결과: `{r.cancelled}`",
                f"- cancel 후 상태: `{r.final_status}`",
            ]
        )
    lines.append("")

    lines.append("## 3. 5 USDT 시장가 매수/매도 왕복 + 3-way 대사")
    lines.append("")
    if report.market_roundtrip is None:
        lines.append("- 실행되지 않음")
    else:
        m = report.market_roundtrip
        for label, order_id, recon in (
            ("매수", m.buy_order_id, m.buy_reconciliation),
            ("매도", m.sell_order_id, m.sell_reconciliation),
        ):
            verdict = "MATCH" if recon.matched else ("PENDING" if recon.pending else "MISMATCH")
            lines.extend(
                [
                    f"### {label}",
                    f"- 주문 id: `{order_id}`",
                    f"- 체결가(거래소): `{recon.exchange_filled_qty}`",
                    f"- 체결가(이력): `{recon.history_filled_qty}`",
                    f"- 로컬 포지션: `{recon.local_position_qty}`",
                    f"- 대사 결과: {verdict} — {recon.notes}",
                    "",
                ]
            )

    if report.notes:
        lines.extend(["## 4. 비고", "", report.notes, ""])

    return "\n".join(lines) + "\n"


def write_report(report: CanaryReport, *, out_dir: Path | None = None) -> Path:
    target_dir = out_dir if out_dir is not None else DEFAULT_REPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"CANARY_{report.run_date}.md"
    path.write_text(render_report(report), encoding="utf-8")
    return path
