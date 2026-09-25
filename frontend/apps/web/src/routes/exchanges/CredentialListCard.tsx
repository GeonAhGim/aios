import type { AccountBalance, CredentialResponse } from "@aios/shared-types";
import { Badge, Button, Card, CardTitle, EmptyState, LoadingState } from "@aios/ui-web";
import { exchangeLabel } from "../../lib/exchangeLabels";
import { credentialScope, LIVE_BLOCKED_NOTICE, type CredentialWithSecretRef } from "./credentialScope";
import { RevokeCredentialError } from "./ExchangeCredentialErrors";
import { useTranslation } from "react-i18next";

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
  const { t } = useTranslation();
  return (
    <Card>
      <CardTitle>{t("legacy.credentialListCard.t1")}</CardTitle>
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
                    {t("legacy.credentialListCard.t2")}{new Date(c.linkedAt).toLocaleDateString()}
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
                    {t("legacy.credentialListCard.t3")}</Button>
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    disabled={scope.isLive || revokingExchange === c.exchange}
                    onClick={() => onRevoke(c.exchange)}
                  >
                    {t("legacy.credentialListCard.t4")}</Button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState>{t("legacy.credentialListCard.t5")}</EmptyState>
      )}

      {revokeError !== null && (
        <div className="mt-4">
          <RevokeCredentialError error={revokeError.error} onRetry={onRetryRevoke} />
        </div>
      )}

      {selectedExchange && balances && (
        <div className="mt-4 rounded-md border border-border bg-surface-hover p-4">
          <p className="mb-2 text-sm text-fg-secondary">{t("legacy.credentialListCard.t6", { exchangeLabel: exchangeLabel(selectedExchange) })}</p>
          {balances.length > 0 ? (
            <ul className="tabular space-y-1 text-sm text-fg">
              {balances.map((b) => (
                <li key={b.asset}>
                  {b.asset}: {b.available} / {b.total}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-fg-muted">{t("legacy.credentialListCard.t7")}</p>
          )}
        </div>
      )}
    </Card>
  );
}
