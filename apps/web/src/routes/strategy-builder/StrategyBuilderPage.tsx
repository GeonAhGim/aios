import { useCreateStrategy, useIndicators, usePreviewStrategy } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import type { PreviewCondition } from "@aios/shared-types";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ConditionGroup } from "./components/ConditionGroup";

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
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ strategyId: string; version: string; status: string } | null>(
    null,
  );

  const indicators = indicatorList?.indicators ?? ["RSI", "SMA", "EMA"];

  async function handlePreview() {
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
      setError(err instanceof ApiError ? err.message : "미리보기에 실패했습니다.");
    }
  }

  async function handleSave() {
    setError(null);
    if (!strategyId.trim()) {
      setError("전략 ID를 입력해주세요.");
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
      setError(err instanceof ApiError ? err.message : "저장에 실패했습니다.");
    }
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">전략 편집기</h1>

        <div className="grid grid-cols-3 gap-4">
          <div className="space-y-1">
            <label className="text-sm text-slate-400">전략 ID</label>
            <input
              type="text"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              placeholder="my-rsi-strategy"
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
          </div>
          <div className="space-y-1">
            <label className="text-sm text-slate-400">대상 자산</label>
            <select
              value={targetAsset}
              onChange={(e) => setTargetAsset(e.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            >
              {TARGET_ASSETS.map((asset) => (
                <option key={asset} value={asset}>
                  {asset}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-sm text-slate-400">거래소</label>
            <select
              value={exchange}
              onChange={(e) => setExchange(e.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            >
              <option value="bitget">bitget</option>
            </select>
          </div>
        </div>

        <ConditionGroup
          title="진입 조건"
          conditions={entryConditions}
          combine={entryCombine}
          onConditionsChange={setEntryConditions}
          onCombineChange={setEntryCombine}
          indicators={indicators}
        />
        <ConditionGroup
          title="청산 조건"
          conditions={exitConditions}
          combine={exitCombine}
          onConditionsChange={setExitConditions}
          onCombineChange={setExitCombine}
          indicators={indicators}
        />
        <ConditionGroup
          title="손절 조건"
          conditions={stopLossConditions}
          combine={stopLossCombine}
          onConditionsChange={setStopLossConditions}
          onCombineChange={setStopLossCombine}
          indicators={indicators}
        />

        {error && <p className="text-sm text-red-400">{error}</p>}

        <div className="flex gap-3">
          <button
            type="button"
            onClick={handlePreview}
            disabled={previewStrategy.isPending}
            className="rounded border border-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50"
          >
            {previewStrategy.isPending ? "미리보기 중..." : "진입 조건 미리보기"}
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={createStrategy.isPending}
            className="rounded bg-slate-100 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-white disabled:opacity-50"
          >
            {createStrategy.isPending ? "저장 중..." : "전략 저장"}
          </button>
        </div>

        {previewStrategy.data && (
          <div className="rounded border border-slate-800 p-4 text-sm text-slate-300">
            <p className="mb-1 text-xs text-amber-400">{previewStrategy.data.disclaimer}</p>
            {previewStrategy.data.message ? (
              <p className="text-slate-500">{previewStrategy.data.message}</p>
            ) : (
              <p>
                최근 {previewStrategy.data.signalIndices.length}개 신호 발생 시점:{" "}
                {previewStrategy.data.signalTimes.slice(0, 5).join(", ")}
                {previewStrategy.data.signalTimes.length > 5 ? " ..." : ""}
              </p>
            )}
          </div>
        )}

        {saved && (
          <div className="rounded border border-emerald-900 bg-emerald-950/30 p-4 text-sm text-emerald-300">
            전략이 저장됐습니다 — {saved.strategyId}@{saved.version} ({saved.status})
          </div>
        )}
      </div>
    </AppShell>
  );
}
