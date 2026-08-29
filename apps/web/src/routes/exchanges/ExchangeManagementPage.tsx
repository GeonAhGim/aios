import {
  useExchangeBalance,
  useExchangeCredentials,
  useRegisterExchangeCredential,
  useRevokeExchangeCredential,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";

const EXCHANGES = ["bitget", "kis"];

export function ExchangeManagementPage() {
  const { data: credentials, isLoading } = useExchangeCredentials();
  const register = useRegisterExchangeCredential();
  const revoke = useRevokeExchangeCredential();
  const [exchange, setExchange] = useState("bitget");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [apiPassphrase, setApiPassphrase] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [selectedExchange, setSelectedExchange] = useState<string | null>(null);
  const { data: balances } = useExchangeBalance(selectedExchange);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await register.mutateAsync({
        exchange,
        apiKey,
        apiSecret,
        apiPassphrase: exchange === "bitget" ? apiPassphrase : undefined,
      });
      setApiKey("");
      setApiSecret("");
      setApiPassphrase("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "등록에 실패했습니다.");
    }
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <h1 className="text-2xl font-semibold text-slate-100">거래소 연동</h1>

        <section className="rounded-lg border border-slate-800 p-6">
          <h2 className="mb-4 text-lg font-medium text-slate-100">연동된 거래소</h2>
          {isLoading ? (
            <p className="text-slate-500">불러오는 중...</p>
          ) : credentials && credentials.length > 0 ? (
            <ul className="divide-y divide-slate-800">
              {credentials.map((c) => (
                <li key={c.id} className="flex items-center justify-between py-3">
                  <div>
                    <p className="font-medium text-slate-100">{c.exchange}</p>
                    <p className="text-sm text-slate-500">
                      {c.isActive ? "활성" : "비활성"} · 연동일{" "}
                      {new Date(c.linkedAt).toLocaleDateString()}
                    </p>
                    {c.withdrawalPermissionWarning && (
                      <p className="mt-1 text-sm text-amber-400">
                        ⚠ {c.withdrawalPermissionWarning}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setSelectedExchange(c.exchange)}
                      className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-300 hover:bg-slate-800"
                    >
                      잔고 조회
                    </button>
                    <button
                      type="button"
                      onClick={() => revoke.mutate(c.exchange)}
                      className="rounded border border-red-900 px-3 py-1 text-sm text-red-400 hover:bg-red-950"
                    >
                      해지
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-slate-500">연동된 거래소가 없습니다.</p>
          )}

          {selectedExchange && balances && (
            <div className="mt-4 rounded border border-slate-800 p-4">
              <p className="mb-2 text-sm text-slate-400">{selectedExchange} 잔고</p>
              {balances.length > 0 ? (
                <ul className="space-y-1 text-sm text-slate-200">
                  {balances.map((b) => (
                    <li key={b.asset}>
                      {b.asset}: {b.available} / {b.total}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-slate-500">잔고 정보가 없습니다.</p>
              )}
            </div>
          )}
        </section>

        <section className="rounded-lg border border-slate-800 p-6">
          <h2 className="mb-4 text-lg font-medium text-slate-100">새 거래소 연동</h2>
          <form onSubmit={handleSubmit} className="max-w-sm space-y-3">
            <div className="space-y-1">
              <label className="text-sm text-slate-400">거래소</label>
              <select
                value={exchange}
                onChange={(e) => setExchange(e.target.value)}
                className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
              >
                {EXCHANGES.map((ex) => (
                  <option key={ex} value={ex}>
                    {ex}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-sm text-slate-400">API Key</label>
              <input
                type="text"
                required
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm text-slate-400">API Secret</label>
              <input
                type="password"
                required
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
              />
            </div>
            {exchange === "bitget" && (
              <div className="space-y-1">
                <label className="text-sm text-slate-400">API Passphrase</label>
                <input
                  type="password"
                  required
                  value={apiPassphrase}
                  onChange={(e) => setApiPassphrase(e.target.value)}
                  className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
                />
              </div>
            )}
            {error && <p className="text-sm text-red-400">{error}</p>}
            <button
              type="submit"
              disabled={register.isPending}
              className="w-full rounded bg-slate-100 px-3 py-2 font-medium text-slate-950 hover:bg-white disabled:opacity-50"
            >
              {register.isPending ? "등록 중..." : "등록"}
            </button>
          </form>
        </section>
      </div>
    </AppShell>
  );
}
