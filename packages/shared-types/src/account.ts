// src/api/schemas/account.py 1:1 대응.

export interface ApprovalSettingsRequest {
  mode: "SOLO" | "DUAL";
  secondApproverContact?: string;
  riskWarningAcknowledged?: boolean;
}

export interface ApprovalSettingsResponse {
  mode: "SOLO" | "DUAL";
  secondApproverContact: string | null;
  mandatoryWaitSeconds: number;
  riskWarning: string | null;
}

export interface WhitelistEntryRequest {
  exchange: string;
  destinationAddress: string;
  label?: string;
  password: string;
  totpCode?: string;
}

export interface WhitelistEntryResponse {
  id: number;
  exchange: string;
  destinationAddress: string;
  label: string | null;
}

export interface DeletionRequest {
  password: string;
}

export interface DeletionResponse {
  status: string;
  deletionEffectiveAt: string;
}
