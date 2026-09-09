// src/foundation/connections/contracts/v1.py(AccountConnectionView/AccountSnapshotView/
// BeginConnectionRequest/ConnectionState/CapabilityScope) + src/api/schemas/foundation/
// connections.py(ConnectionListResponse) 1:1 대응. task-2346(FE-OPS-5).
//
// reconciliation.ts/trust.ts(task-2337/2338)와 동일하게 런타임 파서를 두지 않는다 —
// ApiClientBase.postEnvelope/requestByRoute가 이미 keysToCamel로 응답을 변환해
// 돌려주므로 타입만 옮긴다. SnapshotValueView.value(Decimal)는 walletBalance.ts와
// 동일 관용으로 string이다 — 어디서도 Number/parseFloat을 거치지 않는다.
export type ConnectionState =
  | "PENDING_CONSENT"
  | "CONNECTING"
  | "ACTIVE_READONLY"
  | "DEGRADED"
  | "REVOKED"
  | "DISCONNECTED";

export type CapabilityScope = "READ_BALANCE" | "READ_POSITION" | "READ_ACTIVITY";

export interface AccountConnectionView {
  id: string;
  providerCode: string;
  maskedAccountLabel: string;
  state: ConnectionState;
  capabilityProfile: CapabilityScope[];
  revision: number;
  createdAt: string | null;
  scopeVerified: boolean;
  schemaVersion: string;
}

export interface BeginConnectionRequest {
  providerCode: string;
  opaqueAccountRef: string;
  requestedCapabilityProfile: CapabilityScope[];
}

export interface ConnectionListResponse {
  connections: AccountConnectionView[];
  asOf: string;
}

export interface SnapshotValueView {
  entityType: string;
  entityKey: string;
  value: string;
}

export interface AccountSnapshotView {
  connectionId: string;
  capturedAt: string;
  providerAsOf: string;
  freshness: string;
  currency: string;
  values: SnapshotValueView[];
  schemaVersion: string;
}
