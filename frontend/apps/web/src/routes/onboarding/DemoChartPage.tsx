import { Button, CandlestickChart, EmptyState, LoadingState, PageHeader, Stat, type CandlestickPoint } from "@aios/ui-web";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AppShell } from "../../components/layout/AppShell";
import { runDemoBacktest, type DemoBacktestResult } from "./demoBacktest";
import { findDemoInstrument, getDemoDailyCandles, type DemoOhlcBar } from "./demoDataset";

function toChartPoints(bars: readonly DemoOhlcBar[]): CandlestickPoint[] {
  return bars.map((bar) => ({ time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close }));
}

// task-2632(U-10): 데모 모드 2·3단계(차트 → 백테스트)를 한 화면에 담는다 —
// "첫 백테스트까지 5분" DoD를 화면 전환 최소화로 달성한다. 백테스트 자체는
// 동기 순수 함수(runDemoBacktest)라 즉시 끝나지만, "실행 중" 로딩 상태를
// 사용자가 볼 수 있어야 한다는 공통 DoD를 만족시키기 위해 setTimeout(0)으로
// 한 틱 미룬다.
export function DemoChartPage() {
  const { instrumentId = "" } = useParams<{ instrumentId: string }>();
  const { t } = useTranslation();
  const instrument = findDemoInstrument(instrumentId);
  const bars = useMemo(() => (instrument ? getDemoDailyCandles(instrumentId) : []), [instrument, instrumentId]);
  const chartPoints = useMemo(() => toChartPoints(bars), [bars]);

  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<DemoBacktestResult | null>(null);

  function handleRunBacktest() {
    setIsRunning(true);
    setResult(null);
    window.setTimeout(() => {
      setResult(runDemoBacktest(bars));
      setIsRunning(false);
    }, 0);
  }

  if (!instrument) {
    return (
      <AppShell>
        <div className="max-w-2xl space-y-6">
          <PageHeader title={t("demoMode.chart.pageTitle")} />
          <EmptyState
            action={
              <Link to="/onboarding/demo">
                <Button type="button" size="sm">
                  {t("demoMode.chart.backLink")}
                </Button>
              </Link>
            }
          >
            {t("demoMode.chart.unknownInstrument")}
          </EmptyState>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="max-w-2xl space-y-6">
        <PageHeader title={`${instrument.label} · ${t("demoMode.chart.pageTitle")}`} />
        <div data-testid="demo-chart-canvas">
          <CandlestickChart data={chartPoints} height={280} />
        </div>
        <div>
          <Button type="button" onClick={handleRunBacktest} loading={isRunning} data-testid="demo-backtest-run">
            {t("demoMode.chart.runBacktest")}
          </Button>
        </div>
        {isRunning && <LoadingState />}
        {result && !isRunning && (
          <section data-testid="demo-backtest-summary" className="space-y-3">
            <h2 className="text-sm font-medium text-fg-secondary">{t("demoMode.chart.summary.heading")}</h2>
            <div className="grid grid-cols-3 gap-3">
              <Stat
                label={t("demoMode.chart.summary.finalEquity")}
                value={result.finalEquity.toFixed(2)}
                tone={result.finalEquity >= result.initialEquity ? "success" : "danger"}
              />
              <Stat label={t("demoMode.chart.summary.trades")} value={result.trades} />
              <Stat label={t("demoMode.chart.summary.maxDrawdown")} value={`${result.maxDrawdownPct.toFixed(2)}%`} />
            </div>
          </section>
        )}
        <Link to="/onboarding/demo" className="inline-block text-sm text-fg-muted underline">
          {t("demoMode.chart.backLink")}
        </Link>
      </div>
    </AppShell>
  );
}
