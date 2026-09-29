import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiClient, useAuthStore } from "@aios/shared-hooks";
import { materializeSweepGrid } from "./sweepGrid";
import { createBacktestsClient, type SweepRequestInput, type SweepResultView } from "@aios/api-client";
import type {
  BacktestConfigV2Input,
  BacktestFillView,
  QuickBacktestRequestInput,
  QuickBacktestResultView,
} from "@aios/api-client";
import { ApiError } from "@aios/api-client";
import { createPriceScale } from "@aios/chart-engine/src/core/priceScale";
import { createTimeScale } from "@aios/chart-engine/src/core/timeScale";
import { isScriptCompileErrorDetails, routeApiError, type Timeframe, type Venue } from "@aios/shared-types";
import { Alert, Button, EmptyState, Field, Input } from "@aios/ui-web";
import type { CandlestickPoint } from "@aios/ui-web";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ScriptEditor, type ScriptEditorMarker } from "../../components/ScriptEditor";
import { useTranslation } from "react-i18next";

// BT-13 — 차트에 붙는 즉시 백테스트 패널. 선행 BT-10c(task-1619, POST
// /v1/backtests/quick)를 소비하는 유일한 화면이다. 스크립트 편집은 DSL-13a
// ScriptEditor를, 결과 마커 오버레이는 CH-10 StrategyMarkers(task-1569)가 이미
// 쓰는 timeScale/priceScale 절대좌표 관용을 그대로 따른다 — 새 마커 렌더링
// 방식을 만들지 않는다(decision). 체결 현실성(BacktestConfigV2)은 사용자에게
// 노출하지 않고 마찰 없는 고정 프리셋 하나만 쓴다 — 상세 구성 화면은 BT-1의
// 몫이라 이 패널의 300줄 제약 안에서 새 폼을 만들지 않는다.
const DEFAULT_SCRIPT = [
  "input length: int = 14",
  "input close: series<float> = 0",
  "let rsi_val = ta.rsi(close, length)",
  "signal go_long = rsi_val < 30",
  "plot(rsi_val, 1)",
  "",
].join("\n");

const DEFAULT_INITIAL_CASH = "10000";
const DEFAULT_HEIGHT = 160;

export type RunQuickBacktest = (input: QuickBacktestRequestInput) => Promise<QuickBacktestResultView>;

export interface BacktestPanelProps {
  venue: Venue;
  instrumentId: string;
  timeframe: Timeframe;
  start: string;
  end: string;
  points: readonly CandlestickPoint[];
  runQuickBacktest: RunQuickBacktest;
  height?: number;
  runSweep?: (input: SweepRequestInput) => Promise<SweepResultView>;
}

// TooManyBarsError(BT-10, quick_backtest.py) — details.bars/max는 라우터
// (backtests.py)가 얹어 재전파한다(schema 없음, ApiError.details로만 온다).
// isScriptCompileErrorDetails(script.ts)와 같은 이유로 details 모양을 좁혀야
// 한다 — 둘 다 VALIDATION_INVALID_FIELD라 errorCode만으로는 구분되지 않는다.
function isTooManyBarsDetails(details: unknown): details is { bars: number; max: number } {
  if (typeof details !== "object" || details === null) return false;
  const d = details as Record<string, unknown>;
  return typeof d.bars === "number" && typeof d.max === "number";
}

function buildQuickConfig(venue: Venue): BacktestConfigV2Input {
  return {
    slippage: { kind: "fixed", bps: 0 },
    commission: { venue, makerBps: 0, takerBps: 0, minFee: 0 },
    latencyMs: 0,
    partialFill: { maxParticipationPct: 1 },
    orderTypes: { limit: false, stop: false, oco: false, trailing: false },
    magnifierTf: null,
    costs: { funding: false, borrowApr: null },
    adjustments: { splits: false, dividends: false },
    calendar: "24x7",
  };
}

function TooManyBarsNotice({ bars, max }: { bars: number; max: number }) {
  const { t } = useTranslation();
  return (
    <Alert tone="danger">
      <p>
        {t("legacy.backtestPanel.t1", { bars: bars, max: max })}</p>
    </Alert>
  );
}

function BacktestRunError({ error }: { error: unknown }) {
  const details = error instanceof ApiError ? error.details : undefined;
  if (isTooManyBarsDetails(details)) return <TooManyBarsNotice bars={details.bars} max={details.max} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
    />
  );
}

export function BacktestPanel({
  venue,
  instrumentId,
  timeframe,
  start,
  end,
  points,
  runQuickBacktest,
  height = DEFAULT_HEIGHT,
  runSweep = (input) => createBacktestsClient(import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000", () => useAuthStore.getState().token).runSweep(input),
}: BacktestPanelProps) {
  const { t } = useTranslation();
  const [source, setSource] = useState(DEFAULT_SCRIPT);
  const [initialCash, setInitialCash] = useState(DEFAULT_INITIAL_CASH);
  const mutation = useMutation({ mutationFn: runQuickBacktest });
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [grid, setGrid] = useState('{"length":[7,14,21]}');
  const [dataLineageHash, setDataLineageHash] = useState("");
  const [rollupVersion, setRollupVersion] = useState("");
  const sweep = useMutation({
    mutationFn: async () => {
      let materialized: ReturnType<typeof materializeSweepGrid>;
      try {
        materialized = materializeSweepGrid(grid, source);
        if (!dataLineageHash.trim() || !rollupVersion.trim()) throw new Error("Missing provenance");
      } catch {
        throw new Error(t("sweepPanel.invalid"));
      }
      const combos = [];
      for (const combo of materialized.combos) {
        const compiled = await apiClient.compileScript(combo.scriptSource);
        combos.push({ ...combo, scriptHash: compiled.scriptHash });
      }
      const sweepRequest: SweepRequestInput = {
        venue, instrumentId, timeframe, start, end, initialCash,
        config: buildQuickConfig(venue), axes: materialized.axes, combos,
        metric: "final_equity", dataLineageHash: dataLineageHash.trim(), rollupVersion: rollupVersion.trim(), seed: 0,
      };
      const result = await runSweep(sweepRequest);
      return { sweepRequest, result };
    },
    onSuccess: ({ sweepRequest, result }) => {
      queryClient.setQueryData(["backtest-sweep-results", sweepRequest], result);
      navigate("/backtest/sweep-results", { state: { sweepRequest } });
    },
  });

  const compileErrorDetails =
    mutation.error instanceof ApiError && isScriptCompileErrorDetails(mutation.error.details)
      ? mutation.error.details
      : null;
  const markers: ScriptEditorMarker[] = compileErrorDetails
    ? [{ line: compileErrorDetails.line, col: compileErrorDetails.col, message: compileErrorDetails.code }]
    : [];
  const showGenericError = mutation.isError && !compileErrorDetails;

  function handleSourceChange(next: string): void {
    setSource(next);
    if (mutation.isError || mutation.isSuccess) mutation.reset();
  }

  function handleRun(): void {
    mutation.mutate({
      venue,
      instrumentId,
      timeframe,
      start,
      end,
      initialCash,
      scriptSource: source,
      config: buildQuickConfig(venue),
    });
  }

  const noCandles = points.length === 0;
  const runDisabled = noCandles || mutation.isPending || sweep.isPending || source.trim().length === 0;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const timeDomain = useMemo(() => {
    if (points.length === 0) return null;
    const times = points.map((p) => p.time);
    const from = Math.min(...times);
    const to = Math.max(...times);
    return from < to ? { from, to } : null;
  }, [points]);

  const priceDomain = useMemo(() => {
    if (points.length === 0) return null;
    const prices = points.flatMap((p) => [p.high, p.low]);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    return min < max ? { min, max } : null;
  }, [points]);

  const timeScale = useMemo(
    () => (timeDomain ? createTimeScale({ range: timeDomain, width }) : null),
    [timeDomain, width],
  );
  const priceScale = useMemo(
    () => (priceDomain ? createPriceScale({ range: priceDomain, height }) : null),
    [priceDomain, height],
  );

  const fills = mutation.data?.fills ?? [];
  const fillMarkers: Array<BacktestFillView & { time: number; priceNum: number }> = timeDomain
    ? fills
        .map((fill) => {
          const time = Math.floor(new Date(fill.openTime).getTime() / 1000);
          const priceNum = Number(fill.price);
          if (!Number.isFinite(time) || time < timeDomain.from || time > timeDomain.to) return null;
          return { ...fill, time, priceNum };
        })
        .filter((m): m is BacktestFillView & { time: number; priceNum: number } => m !== null)
    : [];

  return (
    <section aria-label={t("legacy.backtestPanel.ariaLabel2")} className="space-y-3">
      <h2 className="text-sm font-medium text-fg-secondary">{t("legacy.backtestPanel.t3")}</h2>

      <ScriptEditor value={source} onChange={handleSourceChange} markers={markers} disabled={mutation.isPending || sweep.isPending} rows={8} />

      <div className="flex items-end gap-3">
        <Field label={t("legacy.backtestPanel.label4")}>
          <Input
            type="number"
            min="0"
            value={initialCash}
            onChange={(e) => setInitialCash(e.target.value)}
            disabled={mutation.isPending || sweep.isPending}
            data-testid="backtest-initial-cash"
          />
        </Field>
        <Button type="button" onClick={handleRun} disabled={runDisabled} loading={mutation.isPending} data-testid="backtest-run">
          {t("legacy.backtestPanel.t5")}</Button>
        {mutation.isPending && (
          <Button type="button" variant="secondary" onClick={() => mutation.reset()} data-testid="backtest-cancel">
            {t("legacy.backtestPanel.t6")}</Button>
        )}
      </div>

      {noCandles && <EmptyState>{t("legacy.backtestPanel.t7")}</EmptyState>}
      <div className="space-y-2">
        <Field label={t("sweepPanel.grid")}>
          <Input aria-label={t("sweepPanel.grid")} value={grid} onChange={(e) => setGrid(e.target.value)} disabled={sweep.isPending} data-testid="sweep-grid" />
        </Field>
        <p className="text-xs text-fg-muted">{t("sweepPanel.help")}</p>
        <Field label={t("sweepPanel.lineage")}>
          <Input aria-label={t("sweepPanel.lineage")} value={dataLineageHash} onChange={(e) => setDataLineageHash(e.target.value)} disabled={sweep.isPending} data-testid="sweep-lineage" />
        </Field>
        <Field label={t("sweepPanel.rollup")}>
          <Input aria-label={t("sweepPanel.rollup")} value={rollupVersion} onChange={(e) => setRollupVersion(e.target.value)} disabled={sweep.isPending} data-testid="sweep-rollup" />
        </Field>
        <Button onClick={() => sweep.mutate()} disabled={runDisabled} loading={sweep.isPending} data-testid="backtest-sweep-run">{t("sweepPanel.run")}</Button>
        {sweep.isError && <BacktestRunError error={sweep.error} />}
      </div>
      {showGenericError && <BacktestRunError error={mutation.error} />}

      {mutation.data && (
        <dl className="grid grid-cols-3 gap-x-4 gap-y-1 text-xs" data-testid="backtest-summary">
          <dt className="text-fg-muted">{t("legacy.backtestPanel.t8")}</dt>
          <dd>{mutation.data.finalEquity}</dd>
          <dt className="text-fg-muted">{t("legacy.backtestPanel.t9")}</dt>
          <dd>{mutation.data.fills.length}</dd>
          <dt className="text-fg-muted">{t("legacy.backtestPanel.t10")}</dt>
          <dd>{mutation.data.bars}</dd>
        </dl>
      )}

      {timeScale && priceScale && fillMarkers.length > 0 && (
        <div
          ref={containerRef}
          className="relative w-full overflow-hidden rounded-md border border-border bg-surface"
          style={{ height }}
          data-testid="backtest-markers-overlay"
        >
          {fillMarkers.map((fill, index) => (
            <span
              key={`${fill.barIndex}-${index}`}
              role="img"
              aria-label={`${fill.side} ${fill.quantity} @ ${fill.price}`}
              title={`${fill.side} ${fill.quantity} @ ${fill.price}`}
              className="absolute -translate-x-1/2 -translate-y-1/2 text-sm"
              style={{
                left: `${timeScale.timeToX(fill.time)}px`,
                top: `${Number.isFinite(fill.priceNum) ? priceScale.priceToY(fill.priceNum) : height / 2}px`,
              }}
            >
              {fill.side === "BUY" ? "▲" : "▼"}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
