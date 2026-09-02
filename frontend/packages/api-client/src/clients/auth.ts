import type {
  LoginRequest,
  MfaSetupResult,
  MfaVerifyRequest,
  SignupRequest,
  TokenResponse,
  UserResponse,
} from "@aios/shared-types";
import type { AnyConstructor } from "../http";

// FD-11.1/11.2 인증. task-112(28cf21b)로 auth.py/users.py가 ApiResponse
// 봉투를 적용해 이 도메인 전부 requestEnvelope 계열을 쓴다.
export function withAuth<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async register(body: SignupRequest): Promise<TokenResponse> {
      return this.postEnvelope("/auth/register", body);
    }

    async login(body: LoginRequest): Promise<TokenResponse> {
      return this.postEnvelope("/auth/login", body);
    }

    async logout(): Promise<{ status: string }> {
      return this.postEnvelope("/auth/logout");
    }

    async setupMfa(): Promise<MfaSetupResult> {
      return this.postEnvelope("/auth/mfa/setup");
    }

    async verifyMfa(body: MfaVerifyRequest): Promise<{ mfaEnabled: boolean }> {
      return this.postEnvelope("/auth/mfa/verify", body);
    }

    async getMe(): Promise<UserResponse> {
      return this.requestEnvelope("/users/me");
    }
  };
}
