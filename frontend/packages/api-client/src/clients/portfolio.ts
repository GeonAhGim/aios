import type {
  AlertCreateRequest,
  PortfolioView,
  PriceAlert,
  RebalanceRequest,
  RebalanceResult,
  ReportSummary,
  TopupRequestBody,
  WalletBalance,
  WalletTopupRequest,
} from "@aios/shared-types";
import type { AnyConstructor } from "../http";

// FD-19 포트폴리오 / FD-13.11 지갑 / FD-14(신설) 알림 / FD-20 보고서.
// 이 라우터들은 봉투 미적용 — 기존 request 계열 그대로 유지.
export function withPortfolio<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getPortfolio(): Promise<PortfolioView> {
      return this.request("/portfolio");
    }

    async rebalancePortfolio(body: RebalanceRequest, idempotencyKey?: string): Promise<RebalanceResult> {
      return this.postIdempotent("/portfolio/rebalance", body, idempotencyKey);
    }

    async getWalletBalance(): Promise<WalletBalance> {
      return this.request("/wallet/balance");
    }

    async requestTopup(body: TopupRequestBody): Promise<WalletTopupRequest> {
      return this.post("/wallet/topup-requests", body);
    }

    async createAlert(body: AlertCreateRequest): Promise<PriceAlert> {
      return this.post("/alerts", body);
    }

    async listMyAlerts(): Promise<PriceAlert[]> {
      return this.request("/alerts");
    }

    async cancelAlert(alertId: number): Promise<PriceAlert> {
      return this.post(`/alerts/${alertId}/cancel`);
    }

    async generateReport(
      periodStart: string,
      periodEnd: string,
      executionId?: number,
    ): Promise<ReportSummary> {
      return this.request(
        this.withQuery("/reports", {
          period_start: periodStart,
          period_end: periodEnd,
          execution_id: executionId,
        }),
      );
    }
  };
}
