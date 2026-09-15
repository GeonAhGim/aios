import { useCandles, useCreateStrategy, useIndicators, usePreviewStrategy } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  routeApiError,
  type GeneratedConditions,
  type PreviewCondition,
} from "@aios/shared-types";
import { Alert, Button, CandlestickChart, Card, Field, PageHeader, Select } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { ConditionGroup } from "./components/ConditionGroup";
import { StrategyWizardPanel } from "./components/StrategyWizardPanel";
import { ValidationRunPanel } from "./components/ValidationRunPanel";
import { useTranslation } from "react-i18next";

// spec §3.3 에러 taxonomy: 미리보기·저장 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901 패턴).
function StrategyActionError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
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

// 06번 §6.2 Draft 화이트리스트 — src/services/condition_compiler.py::
// TARGET_ASSET_WHITELIST와 1:1.
const TARGET_ASSETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"];

const DEFAULT_ENTRY: PreviewCondition = {
  indicator: "RSI",
  params: { timeperiod: 14 },
  operator: "<",
  threshold: 30,
};
const DEFAULT_EXIT: PreviewCondition = {
  indicator: "RSI",
  params: { timeperiod: 14 },
  operator: ">",
  threshold: 70,
};
const DEFAULT_STOP_LOSS: PreviewCondition = {
  indicator: "close",
  params: {},
  operator: "<",
  threshold: 0,
};

export function StrategyBuilderPage() {
  const { t } = useTranslation();
  const { data: indicatorList } = useIndicators();
  const createStrategy = useCreateStrategy();
  const previewStrategy = usePreviewStrategy();

  const [strategyId, setStrategyId] = useState("");
  const [targetAsset, setTargetAsset] = useState(TARGET_ASSETS[0]);
  const [exchange, setExchange] = useState("bitget");
  const [entryConditions, setEntryConditions] = useState<PreviewCondition[]>([DEFAULT_ENTRY]);
  const [exitConditions, setExitConditions] = useState<PreviewCondition[]>([DEFAULT_EXIT]);
  const [stopLossConditions, setStopLossConditions] = useState<PreviewCondition[]>([
    DEFAULT_STOP_LOSS,
  ]);
  const [entryCombine, setEntryCombine] = useState<"AND" | "OR">("AND");
  const [exitCombine, setExitCombine] = useState<"AND" | "OR">("AND");
  const [stopLossCombine, setStopLossCombine] = useState<"AND" | "OR">("AND");
  const [clientError, setClientError] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<{ strategyId: string; version: string; status: string } | null>(
    null,
  );

  const indicators = indicatorList?.indicators ?? ["RSI", "SMA", "EMA"];
  const { data: candles, isError: candlesFailed } = useCandles({
    exchange,
    symbol: targetAsset,
    timeframe: "1h",
    limit: 200,
  });

  function applyGenerated(generated: GeneratedConditions) {
    setEntryConditions(generated.entryConditions);
    setExitConditions(generated.exitConditions);
    setStopLossConditions(generated.stopLossConditions);
    setEntryCombine(generated.entryCombine);
    setExitCombine(generated.exitCombine);
    setStopLossCombine(generated.stopLossCombine);
  }

  async function handlePreview() {
    setClientError(null);
    setError(null);
    try {
      await previewStrategy.mutateAsync({
        exchange,
        symbol: targetAsset,
        timeframe: "1h",
        limit: 200,
        conditions: entryConditions,
        combine: entryCombine,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error(t("legacy.strategyBuilderPage.t14")));
    }
  }

  async function handleSave() {
    setClientError(null);
    setError(null);
    if (!strategyId.trim()) {
      setClientError(t("legacy.strategyBuilderPage.t15"));
      return;
    }
    try {
      const result = await createStrategy.mutateAsync({
        strategyId,
        version: "1.0.0",
        targetAsset,
        market: "crypto",
        exchange,
        entryConditions,
        exitConditions,
        stopLossConditions,
        entryCombine,
        exitCombine,
        stopLossCombine,
      });
      setSaved({ strategyId: result.strategyId, version: result.version, status: result.status });
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error(t("legacy.strategyBuilderPage.t16")));
    }
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title={t("legacy.strategyBuilderPage.title1")} />

        <div className="grid grid-cols-3 gap-4 rounded-lg border border-border bg-surface p-4">
          <Field label={t("legacy.strategyBuilderPage.label2")}>
            <input
              type="text"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              placeholder="my-rsi-strategy"
              className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-fg placeholder:text-fg-muted outline-none focus:border-accent focus:ring-1 focus:ring-accent"
            />
          </Field>
          <Field label={t("legacy.strategyBuilderPage.label3")}>
            <Select value={targetAsset} onChange={(e) => setTargetAsset(e.target.value)}>
              {TARGET_ASSETS.map((asset) => (
                <option key={asset} value={asset}>
                  {asset}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("legacy.strategyBuilderPage.label4")}>
            <Select value={exchange} onChange={(e) => setExchange(e.target.value)}>
              <option value="bitget">bitget</option>
            </Select>
          </Field>
        </div>

        <Card>
          <h2 className="mb-3 text-lg font-semibold text-fg">
            {targetAsset} · {exchange}
          </h2>
          {candlesFailed ? (
            <p className="text-sm text-fg-muted">
              {t("legacy.strategyBuilderPage.t5", { exchange: exchange })}</p>
          ) : candles && candles.length > 0 ? (
            <CandlestickChart
              data={candles.map((c) => ({
                time: Math.floor(new Date(c.openTime).getTime() / 1000),
                open: Number(c.open),
                high: Number(c.high),
                low: Number(c.low),
                close: Number(c.close),
              }))}
            />
          ) : (
            <p className="text-sm text-fg-muted">{t("legacy.strategyBuilderPage.t6")}</p>
          )}
        </Card>

        <StrategyWizardPanel onApply={applyGenerated} />

        <ConditionGroup
          title={t("legacy.strategyBuilderPage.title7")}
          conditions={entryConditions}
          combine={entryCombine}
          onConditionsChange={setEntryConditions}
          onCombineChange={setEntryCombine}
          indicators={indicators}
        />
        <ConditionGroup
          title={t("legacy.strategyBuilderPage.title8")}
          conditions={exitConditions}
          combine={exitCombine}
          onConditionsChange={setExitConditions}
          onCombineChange={setExitCombine}
          indicators={indicators}
        />
        <ConditionGroup
          title={t("legacy.strategyBuilderPage.title9")}
          conditions={stopLossConditions}
          combine={stopLossCombine}
          onConditionsChange={setStopLossConditions}
          onCombineChange={setStopLossCombine}
          indicators={indicators}
        />

        {clientError && <Alert>{clientError}</Alert>}
        {error !== null && <StrategyActionError error={error} />}

        <div className="flex gap-3">
          <Button
            type="button"
            variant="secondary"
            onClick={handlePreview}
            loading={previewStrategy.isPending}
          >
            {t("legacy.strategyBuilderPage.t10")}</Button>
          <Button type="button" onClick={handleSave} loading={createStrategy.isPending}>
            {t("legacy.strategyBuilderPage.t11")}</Button>
        </div>

        {previewStrategy.data && (
          <div className="rounded-lg border border-border bg-surface p-4 text-sm text-fg-secondary">
            <p className="mb-1 text-xs text-warning">{previewStrategy.data.disclaimer}</p>
            {previewStrategy.data.message ? (
              <p className="text-fg-muted">{previewStrategy.data.message}</p>
            ) : (
              <p>
                {t("legacy.strategyBuilderPage.t12", { length: previewStrategy.data.signalIndices.length, val: " ", val2: previewStrategy.data.signalTimes.slice(0, 5).join(", ") })}
                {previewStrategy.data.signalTimes.length > 5 ? " ..." : ""}
              </p>
            )}
          </div>
        )}

        {saved && (
          <Alert tone="success">
            {t("legacy.strategyBuilderPage.t13", { strategyId: saved.strategyId, version: saved.version, status: saved.status })}</Alert>
        )}

        <ValidationRunPanel
          strategyId={strategyId}
          strategyVersion="1.0.0"
          exchange={exchange}
          symbol={targetAsset}
        />
      </div>
    </AppShell>
  );
}
