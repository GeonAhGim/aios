import type { ExchangePosition, ExchangePositionMoney } from "@aios/api-client";
import { useExchangePositions } from "@aios/shared-hooks";
import { Card, CardTitle, EmptyState, LoadingState } from "@aios/ui-web";
import { useTranslation } from "react-i18next";
import { exchangeLabel } from "../../lib/exchangeLabels";
import { PositionsQueryError } from "../portfolio/PositionsQueryError";

// task-4003(FE-OPS-10c): GET /exchange-credentials/{exchange}/positions(task-4001
// 라우트 등록, task-4002 클라이언트)를 처음으로 실제 화면에 배선한다. 에러 갈래는
// 새로 만들지 않고 PositionsQueryError(task-1524)를 그대로 재사용한다 — 404
// RESOURCE_NOT_FOUND(CredentialNotFoundError, credential_resolver.py:62)는
// "없음" 상태로, EXCHANGE_UNAVAILABLE(503) 등은 재시도 배너로 갈린다.
function moneyText(money: ExchangePositionMoney): string {
  return `${money.amount} ${money.currency}`;
}

const COLUMNS: ReadonlyArray<{ key: keyof ExchangePosition; label: string }> = [
  { key: "symbol", label: "심볼" },
  { key: "quantity", label: "수량" },
  { key: "averageEntryPrice", label: "평단" },
  { key: "unrealizedPnl", label: "미실현손익" },
];

function cellText(position: ExchangePosition, key: keyof ExchangePosition): string {
  const value = position[key];
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && "amount" in value) {
    return moneyText(value as ExchangePositionMoney);
  }
  return "-";
}

interface ExchangePositionsCardProps {
  exchange: string | null;
}

export function ExchangePositionsCard({ exchange }: ExchangePositionsCardProps) {
  const { t } = useTranslation();
  const query = useExchangePositions(exchange);

  return (
    <Card>
      <CardTitle>{t("exchangePositions.title")}</CardTitle>
      {!exchange ? (
        <EmptyState>{t("exchangePositions.selectPrompt")}</EmptyState>
      ) : query.isError ? (
        <PositionsQueryError
          error={query.error}
          notFoundTitle={t("exchangePositions.notFoundTitle")}
          onRetry={() => query.refetch()}
        />
      ) : query.isLoading ? (
        <LoadingState />
      ) : query.data && query.data.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-fg-muted">
              <tr>
                {COLUMNS.map((column) => (
                  <th key={column.key} className="pb-1 font-normal">
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="tabular">
              {query.data.map((position, index) => (
                <tr key={`${position.symbol}-${index}`} className="border-t border-border">
                  {COLUMNS.map((column) => (
                    <td key={column.key} className="py-1 pr-2">
                      {cellText(position, column.key)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState>{t("exchangePositions.empty", { exchange: exchangeLabel(exchange) })}</EmptyState>
      )}
    </Card>
  );
}
