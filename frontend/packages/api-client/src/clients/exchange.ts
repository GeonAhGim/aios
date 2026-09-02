import type {
  AccountBalance,
  CredentialRequest,
  CredentialResponse,
  ExchangeCapability,
} from "@aios/shared-types";
import type { AnyConstructor } from "../http";

// FD-12 거래소 연동 — exchange-credentials 라우터는 봉투 미적용, 기존 경로 유지.
export function withExchange<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async registerExchangeCredential(
      body: CredentialRequest,
      idempotencyKey?: string,
    ): Promise<CredentialResponse> {
      return this.postIdempotent("/exchange-credentials", body, idempotencyKey);
    }

    async listExchangeCredentials(): Promise<CredentialResponse[]> {
      return this.request("/exchange-credentials");
    }

    async revokeExchangeCredential(exchange: string): Promise<{ exchange: string; status: string }> {
      return this.del(`/exchange-credentials/${exchange}`);
    }

    async getExchangeBalance(exchange: string): Promise<AccountBalance[]> {
      return this.request(`/exchange-credentials/${exchange}/balance`);
    }

    async getExchangeCapabilities(exchange: string): Promise<ExchangeCapability> {
      return this.request(`/exchange-credentials/${exchange}/capabilities`);
    }
  };
}
