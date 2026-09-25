import type { ParsedCandleSeries, ParsedQualityVerdict, QualityIssue, Verdict } from "@aios/shared-types";
import { Alert, Badge } from "@aios/ui-web";
import { useTranslation } from "react-i18next";

// spec §3.1 CandleSeries.gaps / QualityVerdict 표시 전용 배지. 서버 라우트가 아직
// 없으므로(task-629 decision) fetch는 하지 않고, 이미 파싱된 결과를 그대로 props로
// 받는다 — PositionPnLCard와 같은 순수 표시 컴포넌트 패턴(차트 라이브러리 미도입).
//
// gaps가 비어있지 않거나 verdict != ACCEPT면 그 사실을 숨기지 않고 사유
// (QualityIssueType)와 함께 노출한다 — 조용히 통과시키면 갭·격리가 있는데도
// 화면상 "정상"으로 보이는 사고가 난다.
interface CandleQualityBadgeProps {
  series: ParsedCandleSeries;
  verdict?: ParsedQualityVerdict;
}

const VERDICT_TONE: Record<Verdict, "success" | "warning" | "danger"> = {
  ACCEPT: "success",
  PARTIAL: "warning",
  QUARANTINE: "danger",
  REJECT: "danger",
};

function issueLabel(issue: QualityIssue): string {
  return `${issue.type}(${issue.severity})`;
}

function VerdictSection({ verdict }: { verdict: ParsedQualityVerdict }) {
  const { t } = useTranslation();
  if (verdict.kind === "unsupported_schema_version") {
    return <Alert tone="danger">{t("legacy.candleQualityBadge.t1", { string: String(verdict.received) })}</Alert>;
  }
  if (verdict.kind !== "ok") {
    return <Alert tone="danger">{t("legacy.candleQualityBadge.t2")}</Alert>;
  }

  const { value } = verdict;
  if (value.verdict === "ACCEPT") {
    return (
      <Badge tone="success" data-testid="verdict-badge">
        {t("legacy.candleQualityBadge.t3")}</Badge>
    );
  }

  return (
    <div className="flex flex-col gap-1" data-testid="verdict-badge">
      <Badge tone={VERDICT_TONE[value.verdict]}>{value.verdict}</Badge>
      <ul className="text-xs text-fg-muted">
        {value.issues.map((issue, index) => (
          <li key={`${issue.type}-${issue.open_time ?? "none"}-${index}`}>{issueLabel(issue)}</li>
        ))}
      </ul>
    </div>
  );
}

export function CandleQualityBadge({ series, verdict }: CandleQualityBadgeProps) {
  const { t } = useTranslation();
  if (series.kind === "unsupported_schema_version") {
    return (
      <div data-testid="candle-quality-badge">
        <Alert tone="danger">{t("legacy.candleQualityBadge.t4", { string: String(series.received) })}</Alert>
      </div>
    );
  }

  if (series.kind !== "ok") {
    return (
      <div data-testid="candle-quality-badge">
        <Alert tone="danger">{t("legacy.candleQualityBadge.t5")}</Alert>
      </div>
    );
  }

  const gapCount = series.value.gaps.length;

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="candle-quality-badge">
      {gapCount > 0 ? (
        <Badge tone="warning" data-testid="gap-badge">
          {t("legacy.candleQualityBadge.t6", { gapCount: gapCount })}</Badge>
      ) : (
        <Badge tone="success" data-testid="gap-badge">
          {t("legacy.candleQualityBadge.t7")}</Badge>
      )}
      {verdict && <VerdictSection verdict={verdict} />}
    </div>
  );
}
