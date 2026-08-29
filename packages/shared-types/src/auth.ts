// src/api/schemas/auth.py, src/api/routers/auth.py, src/services/mfa_service.py 1:1 대응.

export interface SignupRequest {
  email: string;
  password: string;
}

export interface LoginRequest {
  email: string;
  password: string;
  totpCode?: string;
}

export interface TokenResponse {
  accessToken: string;
  tokenType: string;
}

export interface MfaVerifyRequest {
  totpCode: string;
}

export interface MfaSetupResult {
  secret: string;
  provisioningUri: string;
}

export interface UserResponse {
  userId: string;
  email: string;
  displayName: string | null;
  mfaEnabled: boolean;
  status: string;
  isVerifier: boolean;
  isPlatformAdmin: boolean;
}
