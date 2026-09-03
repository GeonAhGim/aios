import { formatCreditAmount, parseWalletBalance, type WalletBalance } from "@aios/shared-types";
import { Alert, Card, CardTitle, Stat } from "@aios/ui-web";

// task-618 — LC-16 응답(available/held/pendingPayout)을 3분할 표시한다. 판정
// 로직(구버전 폴백·경고 판단·구매 가능 여부)은 전부 parseWalletBalance(순수
// 함수, shared-types)가 담당하고 여기서는 그 결과를 그리기만 한다.
interface WalletBalanceCardProps {
  balance: WalletBalance | undefined;
  isLoading?: boolean;
}

export function WalletBalanceCard({ balance, isLoading = false }: WalletBalanceCardProps) {
  if (isLoading) {
    return (
      <Card data-testid="wallet-balance-card">
        <CardTitle>보유 크레딧</CardTitle>
        <p className="text-sm text-fg-muted">불러오는 중...</p>
      </Card>
    );
  }

  const parsed = parseWalletBalance(balance);

  if (parsed.mode === "invalid") {
    return (
      <Card data-testid="wallet-balance-card">
        <CardTitle>보유 크레딧</CardTitle>
        <Alert tone="danger">잔액 정보를 표시할 수 없습니다 ({parsed.reason}).</Alert>
      </Card>
    );
  }

  const purchasableAmount = parsed.mode === "full" ? parsed.available : parsed.balance;

  return (
    <Card data-testid="wallet-balance-card">
      <CardTitle>보유 크레딧</CardTitle>
      <div className="space-y-3">
        <Stat
          label="구매 가능 크레딧"
          value={`${formatCreditAmount(purchasableAmount)} 크레딧`}
          tone={parsed.canPurchase ? "default" : "danger"}
        />

        {parsed.mode === "full" && (
          <dl className="grid grid-cols-2 gap-3 text-sm text-fg-muted">
            <div>
              <dt>주문 보류</dt>
              <dd className="tabular font-medium text-fg">
                {formatCreditAmount(parsed.held)} 크레딧
              </dd>
            </div>
            <div>
              <dt>정산 대기</dt>
              <dd className="tabular font-medium text-fg">
                {formatCreditAmount(parsed.pendingPayout)} 크레딧
              </dd>
            </div>
          </dl>
        )}

        {parsed.mode === "full" && parsed.hasHold && (
          <Alert tone="warning">
            주문 보류 중인 금액이 있어 구매 가능 크레딧에서 제외되었습니다.
          </Alert>
        )}

        {parsed.warnings.length > 0 && (
          <Alert tone="danger">
            잔액 데이터에 이상이 감지되었습니다 ({parsed.warnings.join(", ")}). 관리자에게
            문의해주세요.
          </Alert>
        )}
      </div>
    </Card>
  );
}
