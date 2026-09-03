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
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-19 포트폴리오 / FD-13.11 지갑 / FD-14(신설) 알림 / FD-20 보고서.
// 이 라우터들은 봉투 미적용 — 기존 request 계열 그대로 유지. 경로 문자열은
// apiPaths.ts(task-605) 레지스트리에만 있다(marketData.ts와 동일 관용).
export function withPortfolio<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getPortfolio(): Promise<PortfolioView> {
      return this.request(resolvePath("portfolio.get"));
    }

    // spec §9 PLT-15: idempotencyKey를 필수 인자로 받아 호출부(useIdempotentSubmit)가
    // 키 수명주기를 직접 관리하도록 강제한다 — 누락 시 타입 에러.
    async rebalancePortfolio(body: RebalanceRequest, idempotencyKey: string): Promise<RebalanceResult> {
      return this.postIdempotent(resolvePath("portfolio.rebalance"), body, idempotencyKey);
    }

    async getWalletBalance(): Promise<WalletBalance> {
      return this.request(resolvePath("wallet.balance"));
    }

    async requestTopup(body: TopupRequestBody, idempotencyKey: string): Promise<WalletTopupRequest> {
      return this.postIdempotent(resolvePath("wallet.topupRequests"), body, idempotencyKey);
    }

    async createAlert(body: AlertCreateRequest): Promise<PriceAlert> {
      return this.post(resolvePath("alerts.base"), body);
    }

    async listMyAlerts(): Promise<PriceAlert[]> {
      return this.request(resolvePath("alerts.base"));
    }

    async cancelAlert(alertId: number): Promise<PriceAlert> {
      return this.post(resolvePath("alerts.cancel").replace(":alertId", String(alertId)));
    }

    async generateReport(
      periodStart: string,
      periodEnd: string,
      executionId?: number,
    ): Promise<ReportSummary> {
      return this.request(
        this.withQuery(resolvePath("reports.generate"), {
          period_start: periodStart,
          period_end: periodEnd,
          execution_id: executionId,
        }),
      );
    }
  };
}
