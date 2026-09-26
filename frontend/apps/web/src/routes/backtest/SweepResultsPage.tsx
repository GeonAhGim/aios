import {
  ApiError,
  createBacktestsClient,
  SweepRouteNotImplementedError,
  type SweepAxisInput,
  type SweepMetric,
  type SweepPointResultView,
  type SweepRequestInput,
  type SweepResultView,
  type SweepStabilityView,
} from "@aios/api-client";
import { routeApiError } from "@aios/shared-types";
import { useAuthStore } from "@aios/shared-hooks";
import { Alert, Badge, Card, CardTitle, DIVERGING_DOWN, DIVERGING_UP, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { ErrorMessage } from "../../components/ErrorMessage";
import { AppShell } from "../../components/layout/AppShell";
import { useTranslation } from "react-i18next";

// BT-18(task-7774) — 그리드 스윕 결과(POST /v1/backtests/sweep,
// src/api/routers/backtests.py)를 히트맵·안정성 표면(param_stability.py
// stability_score)·재현 키로 보여준다. 이 화면은 결과 "표시"만 맡고, 스윕 구성/실행
// 화면(CTA)은 아직 이 저널니에 배선되지 않아 범위 밖이라 실행된 스윕의 요청
// 페이로드를 라우터 state로 전달받는다(ListingDetailPage 등과 동일하게
// location.state 관용).
export interface SweepResultsLocationState {
  sweepRequest?: SweepRequestInput;
}

export interface SweepResultsPageProps {
  runSweep?: (input: SweepRequestInput) => Promise<SweepResultView>;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function defaultRunSweep(input: SweepRequestInput): Promise<SweepResultView> {
  const getToken = () => useAuthStore.getState().token;
  return createBacktestsClient(baseUrl, getToken).runSweep(input);
}

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const HEAT_LOW = hexToRgb(DIVERGING_DOWN);
const HEAT_HIGH = hexToRgb(DIVERGING_UP);

function heatColor(t: number): string {
  const clamped = Math.min(1, Math.max(0, t));
  const r = Math.round(HEAT_LOW[0] + (HEAT_HIGH[0] - HEAT_LOW[0]) * clamped);
  const g = Math.round(HEAT_LOW[1] + (HEAT_HIGH[1] - HEAT_LOW[1]) * clamped);
  const b = Math.round(HEAT_LOW[2] + (HEAT_HIGH[2] - HEAT_LOW[2]) * clamped);
  return `rgb(${r}, ${g}, ${b})`;
}

function SweepErrorBanner({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const notImplemented = error instanceof SweepRouteNotImplementedError;
  const routed = routeApiError(error);
  const canRetry = !notImplemented && (routed.kind === "refetch_retry" || routed.kind === "backoff_retry");
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={canRetry ? onRetry : undefined}
    />
  );
}

// 축이 정확히 2개일 때만 격자로 그릴 수 있다(param_stability.py의 ParamGrid도
// 다축을 허용하지만, 화면에 2차원 표로 펼치는 건 이 리프의 스코프다) — 그 외에는
// 안내만 하고 재현 키 표에서 전체 콤보를 확인하게 한다.
function SweepHeatmap({
  axes,
  metric,
  points,
}: {
  axes: SweepAxisInput[];
  metric: SweepMetric;
  points: SweepPointResultView[];
}) {
  const { t } = useTranslation();
  if (axes.length !== 2) {
    return (
      <Card>
        <CardTitle>{t("legacy.sweepResultsPage.t1")}</CardTitle>
        <p className="text-sm text-fg-muted">{t("legacy.sweepResultsPage.t2", { length: axes.length })}</p>
      </Card>
    );
  }
  const [xAxis, yAxis] = axes;
  const values = points.map((p) => Number(p.metricValue)).filter(Number.isFinite);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const range = max - min || 1;
  const findPoint = (x: number, y: number) =>
    points.find((p) => p.axisValues[xAxis.name] === x && p.axisValues[yAxis.name] === y);

  return (
    <Card>
      <CardTitle>{t("legacy.sweepResultsPage.t3", { metric: metric })}</CardTitle>
      <div className="overflow-x-auto">
        <table className="border-collapse text-xs">
          <thead>
            <tr>
              <th className="p-1 text-left text-fg-muted">
                {yAxis.name} \ {xAxis.name}
              </th>
              {xAxis.values.map((x) => (
                <th key={x} className="p-1 text-fg-muted">
                  {x}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[...yAxis.values].reverse().map((y) => (
              <tr key={y}>
                <th className="p-1 text-fg-muted">{y}</th>
                {xAxis.values.map((x) => {
                  const point = findPoint(x, y);
                  const value = point ? Number(point.metricValue) : null;
                  const t = value === null ? 0 : (value - min) / range;
                  return (
                    <td
                      key={x}
                      data-testid={`sweep-cell-${x}-${y}`}
                      title={point ? `${point.comboKey}: ${point.metricValue}` : "데이터 없음"}
                      className="tabular p-1 text-center text-white"
                      style={{ backgroundColor: point ? heatColor(t) : "var(--color-surface-hover)" }}
                    >
                      {point ? point.metricValue : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function SweepStabilityCard({ stability }: { stability: SweepStabilityView | null }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardTitle>{t("legacy.sweepResultsPage.t4")}</CardTitle>
      {stability === null ? (
        <p className="text-sm text-fg-muted">
          {t("legacy.sweepResultsPage.t5")}</p>
      ) : (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <dt className="text-fg-muted">{t("legacy.sweepResultsPage.t6")}</dt>
          <dd className="tabular">
            {Object.entries(stability.bestAxisValues)
              .map(([k, v]) => `${k}=${v}`)
              .join(", ")}
          </dd>
          <dt className="text-fg-muted">{t("legacy.sweepResultsPage.t7")}</dt>
          <dd className="tabular">{stability.neighborMean}</dd>
          <dt className="text-fg-muted">{t("legacy.sweepResultsPage.t8")}</dt>
          <dd className="tabular">{stability.neighborStd}</dd>
          <dt className="text-fg-muted">{t("legacy.sweepResultsPage.t9")}</dt>
          <dd>
            <Badge tone={stability.isolated ? "danger" : "success"}>
              {stability.isolated ? "고립됨(과최적화 의심)" : "안정적"}
            </Badge>
          </dd>
        </dl>
      )}
    </Card>
  );
}

function SweepReproducibilityTable({ points }: { points: SweepPointResultView[] }) {
  const { t } = useTranslation();
  if (points.length === 0) return null;
  return (
    <Card>
      <CardTitle>{t("legacy.sweepResultsPage.t10")}</CardTitle>
      <ul className="divide-y divide-border text-xs">
        {points.map((p) => (
          <li key={p.comboKey} className="flex items-center justify-between gap-4 py-2">
            <span className="text-fg-muted">
              {p.comboKey} (seed={p.seed})
            </span>
            <code className="tabular text-fg">{p.reproducibilityKey}</code>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function SweepResultsPage({ runSweep = defaultRunSweep }: SweepResultsPageProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const request = (location.state as SweepResultsLocationState | null)?.sweepRequest ?? null;

  const query = useQuery({
    queryKey: ["backtest-sweep-results", request],
    queryFn: () => runSweep(request as SweepRequestInput),
    enabled: request !== null,
  });

  return (
    <AppShell>
      <div className="max-w-5xl space-y-6">
        <PageHeader title={t("legacy.sweepResultsPage.title11")} />

        {request === null && (
          <EmptyState>{t("legacy.sweepResultsPage.t12")}</EmptyState>
        )}

        {request !== null && query.isLoading && <LoadingState />}
        {request !== null && query.isError && (
          <SweepErrorBanner error={query.error} onRetry={() => query.refetch()} />
        )}

        {query.data && (
          <>
            <SweepHeatmap axes={query.data.axes} metric={query.data.metric} points={query.data.points} />
            <SweepStabilityCard stability={query.data.stability} />
            <SweepReproducibilityTable points={query.data.points} />
            {query.data.warnings.length > 0 && (
              <Alert tone="warning">
                <ul className="list-disc space-y-1 pl-4">
                  {query.data.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </Alert>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
