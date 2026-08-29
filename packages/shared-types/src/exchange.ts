// src/api/schemas/exchange.py, src/exchanges/common/types.py, src/data/models/trading.py 1:1 대응.

export interface CredentialRequest {
  exchange: string;
  apiKey: string;
  apiSecret: string;
  apiPassphrase?: string; // Bitget 전용
  cano?: string; // KIS 전용
  acntPrdtCd?: string; // KIS 전용
}

export interface CredentialResponse {
  id: number;
  exchange: string;
  isActive: boolean;
  linkedAt: string;
  withdrawalPermissionWarning: string | null;
}

export type AssetClass =
  | "CRYPTO"
  | "KR_EQUITY"
  | "KR_ETF"
  | "KR_ETN"
  | "KR_FUTURES"
  | "KR_OPTION";

export interface ExchangeCapability {
  exchangeName: string;
  supportedAssetClasses: AssetClass[];
  supportsSpot: boolean;
  supportsFutures: boolean;
  supportsOptions: boolean;
  supportsLeverage: boolean;
  supportsWebsocket: boolean;
  maxLeverage: string;
  referenceFeedCoverage: string;
  hasOfficialSandbox: boolean;
}

export interface AccountBalance {
  exchange: string;
  asset: string;
  total: string;
  available: string;
}
