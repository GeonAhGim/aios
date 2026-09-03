import type {
  LoginRequest,
  MfaSetupResult,
  MfaVerifyRequest,
  SignupRequest,
  TokenResponse,
  UserResponse,
} from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-11.1/11.2 인증. task-112(28cf21b)로 auth.py/users.py가 ApiResponse
// 봉투를 적용해 이 도메인 전부 requestEnvelope 계열을 쓴다.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
export function withAuth<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async register(body: SignupRequest): Promise<TokenResponse> {
      return this.postEnvelope(resolvePath("auth.register"), body);
    }

    async login(body: LoginRequest): Promise<TokenResponse> {
      return this.postEnvelope(resolvePath("auth.login"), body);
    }

    async logout(): Promise<{ status: string }> {
      return this.postEnvelope(resolvePath("auth.logout"));
    }

    async setupMfa(): Promise<MfaSetupResult> {
      return this.postEnvelope(resolvePath("auth.mfaSetup"));
    }

    async verifyMfa(body: MfaVerifyRequest): Promise<{ mfaEnabled: boolean }> {
      return this.postEnvelope(resolvePath("auth.mfaVerify"), body);
    }

    async getMe(): Promise<UserResponse> {
      return this.requestEnvelope(resolvePath("auth.me"));
    }
  };
}
