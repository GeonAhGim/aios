import type { AccountBalance, CredentialResponse } from "@aios/shared-types";
import { Badge, Button, Card, CardTitle, EmptyState, LoadingState } from "@aios/ui-web";
import { exchangeLabel } from "../../lib/exchangeLabels";
import { credentialScope, LIVE_BLOCKED_NOTICE, type CredentialWithSecretRef } from "./credentialScope";
import { RevokeCredentialError } from "./ExchangeCredentialErrors";

export function CredentialListCard({
  credentials,
  isLoading,
  revokingExchange,
  selectedExchange,
  balances,
  revokeError,
  onSelectExchange,
  onRevoke,
  onRetryRevoke,
}: {
  credentials: CredentialResponse[] | undefined;
  isLoading: boolean;
  revokingExchange: string | null;
  selectedExchange: string | null;
  balances: AccountBalance[] | undefined;
  revokeError: { exchange: string; error: unknown } | null;
  onSelectExchange: (exchange: string) => void;
  onRevoke: (exchange: string) => void;
  onRetryRevoke: () => void;
}) {
  return (
    <Card>
      <CardTitle>연동된 거래소</CardTitle>
      {isLoading ? (
        <LoadingState />
      ) : credentials && credentials.length > 0 ? (
        <ul className="divide-y divide-border">
          {credentials.map((c) => {
            const scope = credentialScope((c as CredentialWithSecretRef).secretRef);
            return (
              <li key={c.id} className="flex items-center justify-between py-3">
                <div>
                  <div className="flex items-center gap-2">
                    <p className="font-medium text-fg">{exchangeLabel(c.exchange)}</p>
                    <Badge tone={c.isActive ? "success" : "neutral"}>
                      {c.isActive ? "활성" : "비활성"}
                    </Badge>
                    <Badge tone={scope.tone}>{scope.label}</Badge>
                  </div>
                  <p className="text-sm text-fg-muted">
                    연동일 {new Date(c.linkedAt).toLocaleDateString()}
                  </p>
                  {c.withdrawalPermissionWarning && (
                    <p className="mt-1 text-sm text-warning">⚠ {c.withdrawalPermissionWarning}</p>
                  )}
                  {scope.isLive && <p className="mt-1 text-sm text-danger">{LIVE_BLOCKED_NOTICE}</p>}
                </div>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => onSelectExchange(c.exchange)}
                  >
                    잔고 조회
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    disabled={scope.isLive || revokingExchange === c.exchange}
                    onClick={() => onRevoke(c.exchange)}
                  >
                    해지
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState>연동된 거래소가 없습니다.</EmptyState>
      )}

      {revokeError !== null && (
        <div className="mt-4">
          <RevokeCredentialError error={revokeError.error} onRetry={onRetryRevoke} />
        </div>
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
  );
}
