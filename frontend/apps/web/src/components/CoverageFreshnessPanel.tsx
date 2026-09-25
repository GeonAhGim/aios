import type { ResearchSourceStatusView } from "@aios/shared-types";
import { deriveFreshness } from "@aios/api-client";
import { Alert, Badge } from "@aios/ui-web";
import { useTranslation } from "react-i18next";

// RD-18 — 커버리지·신선도 대시보드 패널 + 알림(수집 지연·소스 장애). RD-17
// ResearchPage.tsx가 이미 조회하는 ResearchSourceStatusView[]를 그대로 props로
// 받는 순수 표시 컴포넌트(CoverageBadge.tsx/DataFreshness.tsx와 동일 관용 —
// fetch는 호출부가 하고, 이 컴포넌트는 지연/장애 판정과 렌더만 한다).
//
// RD-8(src/api/routers/research_data.py) 라우터가 아직 없어 lastIngestedAt·
// health는 optional이다(researchData.ts 유령 경로 참조) — 값이 없는 소스는
// "정상"으로 침묵 처리하지 않고 "판정 불가"로 표시한다(RD-A4 추측 금지).
export interface CoverageFreshnessPanelProps {
  sources: ResearchSourceStatusView[];
  now?: Date;
  /** 수집 지연 판정 임계값(초). 시장 데이터(CoverageBadge, 기본 300초)와 달리
   * 리서치 소스는 실시간 스트림이 아니라 배치 수집이므로 기본값을 24시간으로
   * 둔다 — 호출부가 소스별 수집 주기에 맞춰 조정할 수 있다. */
  staleAfterSec?: number;
}

interface SourceRow {
  source: ResearchSourceStatusView;
  isDelayed: boolean;
  isDown: boolean;
  isDegraded: boolean;
  freshnessKnown: boolean;
}

function buildRow(source: ResearchSourceStatusView, now: Date, staleAfterSec: number): SourceRow {
  const freshness = deriveFreshness(source.lastIngestedAt, now, { staleAfterSec });
  return {
    source,
    isDelayed: freshness.kind === "ok" && freshness.isStale === true,
    isDown: source.health === "down",
    isDegraded: source.health === "degraded",
    freshnessKnown: freshness.kind === "ok",
  };
}

export function CoverageFreshnessPanel({ sources, now, staleAfterSec = 86400 }: CoverageFreshnessPanelProps) {
  const { t } = useTranslation();
  const nowDate = now ?? new Date();
  const rows = sources.map((source) => buildRow(source, nowDate, staleAfterSec));

  const delayed = rows.filter((row) => row.isDelayed);
  const down = rows.filter((row) => row.isDown);
  const hasAlert = delayed.length > 0 || down.length > 0;

  return (
    <div data-testid="coverage-freshness-panel" className="space-y-3">
      {hasAlert && (
        <div data-testid="coverage-freshness-alert" className="space-y-2">
          {down.length > 0 && (
            <Alert tone="danger">
              {t("research.coverageFreshness.alert.sourceDown", { count: down.length })}
            </Alert>
          )}
          {delayed.length > 0 && (
            <Alert tone="warning">
              {t("research.coverageFreshness.alert.ingestDelayed", { count: delayed.length })}
            </Alert>
          )}
        </div>
      )}

      {rows.length === 0 ? (
        <p className="text-sm text-fg-muted">{t("research.coverageFreshness.empty")}</p>
      ) : (
        <div className="divide-y divide-border">
          {rows.map((row) => (
            <div
              key={row.source.sourceId}
              data-testid={`coverage-freshness-row-${row.source.sourceId}`}
              className="flex items-center justify-between gap-4 py-2"
            >
              <div>
                <p className="text-sm font-medium text-fg">{row.source.publisher}</p>
                <p className="text-xs text-fg-muted">{row.source.coverage}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {row.isDown && (
                  <Badge tone="danger" data-testid={`coverage-freshness-down-${row.source.sourceId}`}>
                    {t("research.coverageFreshness.badge.down")}
                  </Badge>
                )}
                {!row.isDown && row.isDegraded && (
                  <Badge tone="warning" data-testid={`coverage-freshness-degraded-${row.source.sourceId}`}>
                    {t("research.coverageFreshness.badge.degraded")}
                  </Badge>
                )}
                {row.isDelayed && (
                  <Badge tone="warning" data-testid={`coverage-freshness-delayed-${row.source.sourceId}`}>
                    {t("research.coverageFreshness.badge.delayed")}
                  </Badge>
                )}
                {!row.freshnessKnown && (
                  <Badge tone="neutral" data-testid={`coverage-freshness-unknown-${row.source.sourceId}`}>
                    {t("research.coverageFreshness.badge.unknown")}
                  </Badge>
                )}
                {row.freshnessKnown && !row.isDelayed && !row.isDown && !row.isDegraded && (
                  <Badge tone="success" data-testid={`coverage-freshness-ok-${row.source.sourceId}`}>
                    {t("research.coverageFreshness.badge.ok")}
                  </Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
