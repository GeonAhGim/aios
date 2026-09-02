import {
  useExchangeBalance,
  useExchangeCredentials,
  useRegisterExchangeCredential,
  useRevokeExchangeCredential,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  Badge,
  Button,
  Card,
  CardTitle,
  EmptyState,
  Field,
  Input,
  LoadingState,
  PageHeader,
  Select,
} from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { exchangeLabel } from "../../lib/exchangeLabels";

const EXCHANGES = ["bitget", "kis"];

export function ExchangeManagementPage() {
  const { data: credentials, isLoading } = useExchangeCredentials();
  const register = useRegisterExchangeCredential();
  const revoke = useRevokeExchangeCredential();
  const [exchange, setExchange] = useState("bitget");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [apiPassphrase, setApiPassphrase] = useState("");
  const [error, setError] = useState<ApiError | Error | null>(null);
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
      setError(err instanceof Error ? err : new Error("등록에 실패했습니다."));
    }
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title="거래소 연동" />

        <Card>
          <CardTitle>연동된 거래소</CardTitle>
          {isLoading ? (
            <LoadingState />
          ) : credentials && credentials.length > 0 ? (
            <ul className="divide-y divide-border">
              {credentials.map((c) => (
                <li key={c.id} className="flex items-center justify-between py-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-fg">{exchangeLabel(c.exchange)}</p>
                      <Badge tone={c.isActive ? "success" : "neutral"}>
                        {c.isActive ? "활성" : "비활성"}
                      </Badge>
                    </div>
                    <p className="text-sm text-fg-muted">
                      연동일 {new Date(c.linkedAt).toLocaleDateString()}
                    </p>
                    {c.withdrawalPermissionWarning && (
                      <p className="mt-1 text-sm text-warning">⚠ {c.withdrawalPermissionWarning}</p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => setSelectedExchange(c.exchange)}
                    >
                      잔고 조회
                    </Button>
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      onClick={() => revoke.mutate(c.exchange)}
                    >
                      해지
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>연동된 거래소가 없습니다.</EmptyState>
          )}

          {selectedExchange && balances && (
            <div className="mt-4 rounded-md border border-border bg-surface-hover p-4">
              <p className="mb-2 text-sm text-fg-secondary">{exchangeLabel(selectedExchange)} 잔고</p>
              {balances.length > 0 ? (
                <ul className="tabular space-y-1 text-sm text-fg">
                  {balances.map((b) => (
                    <li key={b.asset}>
                      {b.asset}: {b.available} / {b.total}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-fg-muted">잔고 정보가 없습니다.</p>
              )}
            </div>
          )}
        </Card>

        <Card className="max-w-lg">
          <CardTitle>새 거래소 연동</CardTitle>
          <form onSubmit={handleSubmit} className="space-y-3">
            <Field label="거래소">
              <Select value={exchange} onChange={(e) => setExchange(e.target.value)}>
                {EXCHANGES.map((ex) => (
                  <option key={ex} value={ex}>
                    {exchangeLabel(ex)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="API Key">
              <Input type="text" required value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
            </Field>
            <Field label="API Secret">
              <Input
                type="password"
                required
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
              />
            </Field>
            {exchange === "bitget" && (
              <Field label="API Passphrase">
                <Input
                  type="password"
                  required
                  value={apiPassphrase}
                  onChange={(e) => setApiPassphrase(e.target.value)}
                />
              </Field>
            )}
            {error && <ErrorMessage traceId={error instanceof ApiError ? error.traceId : null} />}
            <Button type="submit" loading={register.isPending} className="w-full">
              등록
            </Button>
          </form>
        </Card>
      </div>
    </AppShell>
  );
}
