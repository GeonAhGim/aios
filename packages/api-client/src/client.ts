import type {
  ExecutionCardResponse,
  LoginRequest,
  MfaSetupResult,
  MfaVerifyRequest,
  PortfolioView,
  RiskProfileHistoryEntry,
  RiskProfileResponse,
  SignupRequest,
  SuitabilityAnswers,
  TokenResponse,
  UserResponse,
} from "@aios/shared-types";
import { keysToCamel, keysToSnake } from "./caseConvert";

export class ApiError extends Error {
  statusCode: number;

  constructor(statusCode: number, message: string) {
    super(message);
    this.statusCode = statusCode;
  }
}

function extractDetailMessage(body: unknown): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === "object" && item !== null && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : JSON.stringify(item),
        )
        .join(", ");
    }
  }
  return "요청을 처리할 수 없습니다.";
}

export class AiosApiClient {
  private baseUrl: string;
  private getToken: () => string | null;

  constructor(baseUrl: string, getToken: () => string | null) {
    this.baseUrl = baseUrl;
    this.getToken = getToken;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = this.getToken();
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });

    if (response.status === 204) {
      return undefined as T;
    }

    const text = await response.text();
    const body: unknown = text ? JSON.parse(text) : undefined;

    if (!response.ok) {
      throw new ApiError(response.status, extractDetailMessage(body));
    }

    return keysToCamel<T>(body);
  }

  private post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  // ---- FD-11.1/11.2 인증 ----
  async register(body: SignupRequest): Promise<TokenResponse> {
    return this.post("/auth/register", body);
  }

  async login(body: LoginRequest): Promise<TokenResponse> {
    return this.post("/auth/login", body);
  }

  async logout(): Promise<{ status: string }> {
    return this.post("/auth/logout");
  }

  async setupMfa(): Promise<MfaSetupResult> {
    return this.post("/auth/mfa/setup");
  }

  async verifyMfa(body: MfaVerifyRequest): Promise<{ mfaEnabled: boolean }> {
    return this.post("/auth/mfa/verify", body);
  }

  async getMe(): Promise<UserResponse> {
    return this.request("/users/me");
  }

  // ---- FD-15.1/15.2 적합성평가 ----
  async submitRiskAssessment(body: SuitabilityAnswers): Promise<RiskProfileResponse> {
    return this.post("/users/me/risk-assessment", body);
  }

  async getRiskProfile(): Promise<RiskProfileResponse | null> {
    try {
      return await this.request("/users/me/risk-profile");
    } catch (e) {
      if (e instanceof ApiError && e.statusCode === 404) return null;
      throw e;
    }
  }

  async getRiskProfileHistory(): Promise<RiskProfileHistoryEntry[]> {
    return this.request("/users/me/risk-profile/history");
  }

  // ---- FD-19 포트폴리오 ----
  async getPortfolio(): Promise<PortfolioView> {
    return this.request("/portfolio");
  }

  // ---- FD-16 실행 제어판 ----
  async listExecutions(): Promise<ExecutionCardResponse[]> {
    return this.request("/executions");
  }
}
