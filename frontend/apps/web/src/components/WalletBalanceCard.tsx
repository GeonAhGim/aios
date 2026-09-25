import { formatCreditAmount, parseWalletBalance, type WalletBalance } from "@aios/shared-types";
import { Alert, Card, CardTitle, Stat } from "@aios/ui-web";
import { useTranslation } from "react-i18next";

// task-618 — LC-16 응답(available/held/pendingPayout)을 3분할 표시한다. 판정
// 로직(구버전 폴백·경고 판단·구매 가능 여부)은 전부 parseWalletBalance(순수
// 함수, shared-types)가 담당하고 여기서는 그 결과를 그리기만 한다.
interface WalletBalanceCardProps {
  balance: WalletBalance | undefined;
  isLoading?: boolean;
}

export function WalletBalanceCard({ balance, isLoading = false }: WalletBalanceCardProps) {
  const { t } = useTranslation();
  if (isLoading) {
    return (
      <Card data-testid="wallet-balance-card">
        <CardTitle>{t("legacy.walletBalanceCard.t1")}</CardTitle>
        <p className="text-sm text-fg-muted">{t("legacy.walletBalanceCard.t2")}</p>
      </Card>
    );
  }

  const parsed = parseWalletBalance(balance);

  if (parsed.mode === "invalid") {
    return (
      <Card data-testid="wallet-balance-card">
        <CardTitle>{t("legacy.walletBalanceCard.t3")}</CardTitle>
        <Alert tone="danger">{t("legacy.walletBalanceCard.t4", { reason: parsed.reason })}</Alert>
      </Card>
    );
  }

  const purchasableAmount = parsed.mode === "full" ? parsed.available : parsed.balance;

  return (
    <Card data-testid="wallet-balance-card">
      <CardTitle>{t("legacy.walletBalanceCard.t5")}</CardTitle>
      <div className="space-y-3">
        <Stat
          label={t("legacy.walletBalanceCard.label6")}
          value={`${formatCreditAmount(purchasableAmount)} 크레딧`}
          tone={parsed.canPurchase ? "default" : "danger"}
        />

        {parsed.mode === "full" && (
          <dl className="grid grid-cols-2 gap-3 text-sm text-fg-muted">
            <div>
              <dt>{t("legacy.walletBalanceCard.t7")}</dt>
              <dd className="tabular font-medium text-fg">
                {t("legacy.walletBalanceCard.t8", { formatCreditAmount: formatCreditAmount(parsed.held) })}</dd>
            </div>
            <div>
              <dt>{t("legacy.walletBalanceCard.t9")}</dt>
              <dd className="tabular font-medium text-fg">
                {t("legacy.walletBalanceCard.t10", { formatCreditAmount: formatCreditAmount(parsed.pendingPayout) })}</dd>
            </div>
          </dl>
        )}

        {parsed.mode === "full" && parsed.hasHold && (
          <Alert tone="warning">
            {t("legacy.walletBalanceCard.t11")}</Alert>
        )}

        {parsed.warnings.length > 0 && (
          <Alert tone="danger">
            {t("legacy.walletBalanceCard.t12", { val: parsed.warnings.join(", ") })}</Alert>
        )}
      </div>
    </Card>
  );
}
