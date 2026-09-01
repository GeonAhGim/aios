import type {
  AccountBalance,
  ApprovalRequest,
  DisputeDetail,
  DisputeResolutionResult,
  DisputeResolveRequest,
  DisputeSummary,
  QueuedListing,
  SellerSuspensionResult,
  SuspendSellerRequest,
  UserStatusChangeResult,
  UserSummary,
  ApprovalSettingsRequest,
  ApprovalSettingsResponse,
  CredentialRequest,
  CredentialResponse,
  DeletionRequest,
  DeletionResponse,
  DeviceTokenRecord,
  DeviceTokenRegisterRequest,
  DisputeCreateRequest,
  DisputeResponse,
  ExchangeCapability,
  ExecutionCardResponse,
  ExecutionCreateRequest,
  ExecutionResponse,
  GeneratedConditions,
  IndicatorComputeResponse,
  IndicatorListResponse,
  ListingCreateRequest,
  ListingResponse,
  ListingSearchResponse,
  LoginRequest,
  MfaSetupResult,
  MfaVerifyRequest,
  NotificationHistoryEntry,
  NotificationPreferences,
  PlatformListingCreateRequest,
  PortfolioView,
  PreferenceUpdateResult,
  PreviewRequest,
  PreviewResponse,
  PromptGenerateRequest,
  PurchaseCreateRequest,
  PurchaseResponse,
  RebalanceRequest,
  RebalanceResult,
  ReportSummary,
  ReviewCreateRequest,
  ReviewListResponse,
  ReviewResponse,
  RiskProfileHistoryEntry,
  RiskProfileResponse,
  SignupRequest,
  StrategyCreateRequest,
  StrategyDefinition,
  StrategyDetailResponse,
  StrategyResponse,
  StrategySummary,
  SuitabilityAnswers,
  TokenResponse,
  TopupRequestBody,
  UserResponse,
  VerificationDecisionRequest,
  WalletBalance,
  WalletTopupConfirmResult,
  WalletTopupPage,
  WalletTopupRequest,
  WhitelistEntryRequest,
  WhitelistEntryResponse,
  WizardGenerateRequest,
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

  private put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PUT",
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  private patch<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PATCH",
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  private del<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "DELETE" });
  }

  private withQuery(path: string, params: Record<string, string | number | undefined>): string {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) search.set(key, String(value));
    }
    const qs = search.toString();
    return qs ? `${path}?${qs}` : path;
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

  async rebalancePortfolio(body: RebalanceRequest): Promise<RebalanceResult> {
    return this.post("/portfolio/rebalance", body);
  }

  // ---- FD-13.11 지갑(크레딧) ----
  async getWalletBalance(): Promise<WalletBalance> {
    return this.request("/wallet/balance");
  }

  async requestTopup(body: TopupRequestBody): Promise<WalletTopupRequest> {
    return this.post("/wallet/topup-requests", body);
  }

  // ---- FD-20 보고서 ----
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

  // ---- FD-16 실행 제어판 ----
  async listExecutions(): Promise<ExecutionCardResponse[]> {
    return this.request("/executions");
  }

  async createExecution(body: ExecutionCreateRequest): Promise<ExecutionResponse> {
    return this.post("/executions", body);
  }

  async startExecution(executionId: number): Promise<ExecutionResponse> {
    return this.post(`/executions/${executionId}/start`);
  }

  async pauseExecution(executionId: number): Promise<ExecutionResponse> {
    return this.post(`/executions/${executionId}/pause`);
  }

  async retireExecution(
    executionId: number,
    liquidation: "IMMEDIATE_MARKET" | "KEEP_POSITIONS" = "KEEP_POSITIONS",
  ): Promise<ExecutionResponse> {
    return this.post(`/executions/${executionId}/retire`, { liquidation });
  }

  async convertToLive(
    executionId: number,
    body: { allocatedCapital: string; currency: string; exchange: string },
  ): Promise<ExecutionResponse> {
    return this.post(`/executions/${executionId}/convert-to-live`, body);
  }

  // ---- FD-12 거래소 연동 ----
  async registerExchangeCredential(body: CredentialRequest): Promise<CredentialResponse> {
    return this.post("/exchange-credentials", body);
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

  // ---- FD-14 전략 편집기 ----
  async listIndicators(): Promise<IndicatorListResponse> {
    return this.request("/strategy-builder/indicators");
  }

  async listMyStrategies(): Promise<StrategySummary[]> {
    return this.request("/strategy-builder/strategies");
  }

  async computeIndicator(
    name: string,
    params: { exchange: string; symbol: string; timeframe?: string; period?: number },
  ): Promise<IndicatorComputeResponse> {
    return this.request(
      this.withQuery(`/strategy-builder/indicators/${name}/compute`, params),
    );
  }

  async createStrategy(body: StrategyCreateRequest): Promise<StrategyResponse> {
    return this.post("/strategy-builder/strategies", body);
  }

  async getStrategy(strategyId: string, version: string): Promise<StrategyDetailResponse> {
    return this.request(`/strategy-builder/strategies/${strategyId}/${version}`);
  }

  async previewStrategy(body: PreviewRequest): Promise<PreviewResponse> {
    return this.post("/strategy-builder/preview", body);
  }

  async generateWizardStrategy(body: WizardGenerateRequest): Promise<GeneratedConditions> {
    return this.post("/strategy-builder/wizard", body);
  }

  async generateFromPrompt(body: PromptGenerateRequest): Promise<GeneratedConditions> {
    return this.post("/strategy-builder/generate-from-prompt", body);
  }

  // ---- FD-13 마켓플레이스 ----
  async createListing(body: ListingCreateRequest): Promise<ListingResponse> {
    return this.post("/marketplace/listings", body);
  }

  async searchListings(params: {
    assetClass?: string;
    exchange?: string;
    maxPrice?: string;
    page?: number;
    pageSize?: number;
  }): Promise<ListingSearchResponse> {
    return this.request(
      this.withQuery("/marketplace/listings", {
        asset_class: params.assetClass,
        exchange: params.exchange,
        max_price: params.maxPrice,
        page: params.page,
        page_size: params.pageSize,
      }),
    );
  }

  async submitForVerification(listingId: number): Promise<ListingResponse> {
    return this.post(`/marketplace/listings/${listingId}/submit-verification`);
  }

  async verifyListing(
    listingId: number,
    body: VerificationDecisionRequest,
  ): Promise<{ listingId: number; status: string; rejectionReason: string | null }> {
    return this.post(`/marketplace/listings/${listingId}/verify`, body);
  }

  async purchaseListing(
    listingId: number,
    body: PurchaseCreateRequest,
    idempotencyKey: string,
  ): Promise<PurchaseResponse> {
    return this.request(`/marketplace/listings/${listingId}/purchase`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(keysToSnake(body)),
    });
  }

  async getStrategyDefinition(strategyId: string, version: string): Promise<StrategyDefinition> {
    return this.request(`/marketplace/strategies/${strategyId}/${version}`);
  }

  async createReview(listingId: number, body: ReviewCreateRequest): Promise<ReviewResponse> {
    return this.post(`/marketplace/listings/${listingId}/reviews`, body);
  }

  async listReviews(listingId: number): Promise<ReviewListResponse> {
    return this.request(`/marketplace/listings/${listingId}/reviews`);
  }

  async submitDispute(body: DisputeCreateRequest): Promise<DisputeResponse> {
    return this.post("/marketplace/disputes", body);
  }

  // ---- FD-11.3/11.5/11.6 계정 설정 ----
  async getApprovalSettings(): Promise<ApprovalSettingsResponse> {
    return this.request("/users/me/approval-settings");
  }

  async updateApprovalSettings(body: ApprovalSettingsRequest): Promise<ApprovalSettingsResponse> {
    return this.put("/users/me/approval-settings", body);
  }

  async listWhitelistEntries(): Promise<WhitelistEntryResponse[]> {
    return this.request("/users/me/withdrawal-whitelist");
  }

  async registerWhitelistEntry(body: WhitelistEntryRequest): Promise<WhitelistEntryResponse> {
    return this.post("/users/me/withdrawal-whitelist", body);
  }

  async requestAccountDeletion(body: DeletionRequest): Promise<DeletionResponse> {
    return this.post("/users/me/delete", body);
  }

  // ---- FD-17 알림 ----
  async getNotificationHistory(eventType?: string): Promise<NotificationHistoryEntry[]> {
    return this.request(this.withQuery("/notifications/history", { event_type: eventType }));
  }

  async getNotificationPreferences(): Promise<NotificationPreferences> {
    return this.request("/notifications/preferences");
  }

  async updateNotificationPreferences(
    changes: NotificationPreferences,
  ): Promise<PreferenceUpdateResult> {
    return this.put("/notifications/preferences", changes);
  }

  // ---- FD-21 디바이스 토큰 ----
  async registerDeviceToken(body: DeviceTokenRegisterRequest): Promise<DeviceTokenRecord> {
    return this.post("/device-tokens", body);
  }

  async deactivateDeviceToken(deviceId: number): Promise<{ deviceId: string; status: string }> {
    return this.del(`/device-tokens/${deviceId}`);
  }

  // ---- FD-18 관리자 도구 ----
  async getVerificationQueue(): Promise<QueuedListing[]> {
    return this.request("/admin/verification-queue");
  }

  async listAdminDisputes(disputeStatus?: string): Promise<DisputeSummary[]> {
    return this.request(this.withQuery("/admin/disputes", { dispute_status: disputeStatus }));
  }

  async getAdminDispute(disputeId: number): Promise<DisputeDetail> {
    return this.request(`/admin/disputes/${disputeId}`);
  }

  async resolveDispute(
    disputeId: number,
    body: DisputeResolveRequest,
  ): Promise<DisputeResolutionResult> {
    return this.post(`/admin/disputes/${disputeId}/resolve`, body);
  }

  async listAdminUsers(emailSearch?: string): Promise<UserSummary[]> {
    return this.request(this.withQuery("/admin/users", { email_search: emailSearch }));
  }

  async changeUserStatus(userId: string, status: string): Promise<UserStatusChangeResult> {
    return this.patch(`/admin/users/${userId}/status`, { status });
  }

  async suspendSeller(
    userId: string,
    body: SuspendSellerRequest,
  ): Promise<SellerSuspensionResult> {
    return this.post(`/admin/users/${userId}/suspend-seller`, body);
  }

  async listPendingTopups(page = 1, pageSize = 20): Promise<WalletTopupPage> {
    return this.request(
      this.withQuery("/admin/wallet/topups/pending", { page, page_size: pageSize }),
    );
  }

  async confirmTopup(
    topupId: number,
    idempotencyKey: string,
  ): Promise<WalletTopupConfirmResult> {
    return this.request(`/admin/wallet/topups/${topupId}/confirm`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    });
  }

  async createPlatformListing(body: PlatformListingCreateRequest): Promise<ListingResponse> {
    return this.post("/admin/marketplace/platform-listings", body);
  }

  async approveRequest(requestId: number): Promise<ApprovalRequest> {
    return this.post(`/admin/approval-requests/${requestId}/approve`);
  }

  async rejectRequest(requestId: number): Promise<ApprovalRequest> {
    return this.post(`/admin/approval-requests/${requestId}/reject`);
  }
}
