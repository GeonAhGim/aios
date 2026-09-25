import { deriveFreshness } from "@aios/api-client";
import { Badge } from "@aios/ui-web";
import { useTranslation } from "react-i18next";

// spec §3.3 ApiResponse.meta.as_of를 화면에 노출하는 표시 전용 컴포넌트.
// 판정 로직은 deriveFreshness(순수 함수)가 전담하고, 여기서는 그 결과를
// "기준 시각 · N분 전" 문구와 stale 배지로 그리기만 한다.
interface DataFreshnessProps {
  asOf: string | null | undefined;
  staleAfterSec?: number;
  now?: Date;
}

function formatAge(ageSec: number): string {
  const minutes = Math.floor(ageSec / 60);
  if (minutes < 1) return "방금 전";
  return `${minutes}분 전`;
}

export function DataFreshness({ asOf, staleAfterSec = 300, now }: DataFreshnessProps) {
  const { t } = useTranslation();
  const freshness = deriveFreshness(asOf, now ?? new Date(), { staleAfterSec });

  if (freshness.kind !== "ok") {
    return (
      <span className="text-xs text-fg-muted" data-testid="data-freshness">
        {t("legacy.dataFreshness.t1")}</span>
    );
  }

  return (
    <span className="inline-flex items-center gap-2 text-xs text-fg-muted" data-testid="data-freshness">
      <span>{t("legacy.dataFreshness.t2", { formatAge: formatAge(freshness.ageSec) })}</span>
      {freshness.isStale && (
        <Badge tone="warning" data-testid="data-freshness-stale-badge">
          {t("legacy.dataFreshness.t3")}</Badge>
      )}
    </span>
  );
}
