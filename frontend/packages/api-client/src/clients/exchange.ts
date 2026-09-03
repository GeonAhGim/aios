import type {
  AccountBalance,
  CredentialRequest,
  CredentialResponse,
  ExchangeCapability,
} from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-12 거래소 연동 — exchange-credentials 라우터는 봉투 미적용, 기존 경로 유지.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
export function withExchange<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async registerExchangeCredential(
      body: CredentialRequest,
      idempotencyKey?: string,
    ): Promise<CredentialResponse> {
      return this.postIdempotent(resolvePath("exchange.credentials.base"), body, idempotencyKey);
    }

    async listExchangeCredentials(): Promise<CredentialResponse[]> {
      return this.request(resolvePath("exchange.credentials.base"));
    }

    async revokeExchangeCredential(exchange: string): Promise<{ exchange: string; status: string }> {
      return this.del(resolvePath("exchange.credentials.item").replace(":exchange", exchange));
    }

    async getExchangeBalance(exchange: string): Promise<AccountBalance[]> {
      return this.request(resolvePath("exchange.credentials.balance").replace(":exchange", exchange));
    }

    async getExchangeCapabilities(exchange: string): Promise<ExchangeCapability> {
      return this.request(resolvePath("exchange.credentials.capabilities").replace(":exchange", exchange));
    }
  };
}
