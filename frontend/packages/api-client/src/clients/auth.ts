import type {
  LoginRequest,
  MfaSetupResult,
  MfaVerifyRequest,
  ParsedTokenPair,
  SignupRequest,
  UserResponse,
} from "@aios/shared-types";
import { parseTokenPair } from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import { keysToSnake } from "../caseConvert";
import { unwrap } from "../envelope";
import type { AnyConstructor } from "../http";
import type { TokenRefreshHandler } from "../tokenRefresh";
import type { TokenStore } from "../tokenStore";

// FD-11.1/11.2 + §3.4/§9 PLT-24 인증. task-1324: origin/main 0a68f86에서 확인한
// src/api/routers/auth.py는 login/refresh/logout-all 전부
// ApiResponse[TokenPairResponse](access_token/refresh_token/token_type/
// expires_in/session_id)를 반환한다 — 예전 단일 비회전 TokenResponse는
// 라우터에서 더 이상 쓰지 않는다(src/api/schemas/auth.py 주석 참조).
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다.

export class TokenPairFormatError extends Error {}

// postEnvelope는 이미 data를 camelCase로 바꿔 돌려주므로(http.ts
// executeRequestEnvelope), task-426 parseTokenPair(snake_case 계약 검증)를
// 새로 재구현하지 않고 그대로 재사용하려면 keysToSnake로 한 번 되돌린다.
// 필드 누락·token_type 위반 등은 여기서 null로 잡히고, 그 실패를 조용히
// 삼키는 대신 TokenPairFormatError로 표면화한다(무응답 은폐 금지).
function toParsedTokenPair(data: unknown): ParsedTokenPair {
  const parsed = parseTokenPair(keysToSnake(data));
  if (!parsed) {
    throw new TokenPairFormatError("서버 응답이 §3.4 TokenPairResponse 계약과 다릅니다.");
  }
  return parsed;
}

export function withAuth<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async register(body: SignupRequest): Promise<ParsedTokenPair> {
      return toParsedTokenPair(await this.postEnvelope(resolvePath("auth.register"), body));
    }

    async login(body: LoginRequest): Promise<ParsedTokenPair> {
      return toParsedTokenPair(await this.postEnvelope(resolvePath("auth.login"), body));
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
      return this.requestByRoute("auth.me");
    }
  };
}

export interface AuthRefreshHandlerOptions {
  baseUrl: string;
  // tokenStore.ts와 구조적으로 호환되는 최소 계약만 요구한다(logout.ts의
  // LogoutTokenStore와 동일 관용) — setPair가 내부에서 parseTokenPair를 이미
  // 호출하므로 이 핸들러는 파싱을 다시 하지 않는다.
  store: Pick<TokenStore, "setPair" | "getRefresh" | "peekSessionId">;
}

// POST /auth/refresh 실행부 — tokenRefresh.ts(task-386/1020/1166)의
// TokenRefreshHandler 계약대로 configureTokenRefreshHandler에 등록해 쓴다.
// ApiClientBase.postEnvelope를 쓰지 않고 raw fetch로 직접 호출하는 이유:
// refresh 요청 자체가 401 AUTH_TOKEN_EXPIRED를 받으면 http.ts의
// handleAuthFailure가 refreshAccessToken()을 다시 호출하는데, 그 함수는
// 이미 실행 중인 이 handler의 결과를 기다리는 단일 in-flight 프라미스를
// 공유하므로(tokenRefresh.ts) 재귀 호출이 자기 자신을 기다려 영원히
// 끝나지 않는다 — logout.ts가 같은 이유로 ApiClientBase를 우회하는 것과
// 동일하다. 이 함수는 진행 여부만 boolean으로 알린다(throw 금지 — 실패
// 경로 전부 false로 수렴해 tokenRefresh.ts가 clearHandler를 호출하게 둔다).
export function createAuthTokenRefreshHandler(options: AuthRefreshHandlerOptions): TokenRefreshHandler {
  const { baseUrl, store } = options;
  return async () => {
    const sessionId = store.peekSessionId();
    const refreshToken = store.getRefresh();
    if (!sessionId || !refreshToken) return false;

    try {
      const response = await fetch(`${baseUrl}${resolvePath("auth.refresh")}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, refresh_token: refreshToken }),
      });
      if (!response.ok) return false;

      const body: unknown = await response.json();
      const result = unwrap<unknown>(body); // 봉투가 아닌 응답은 throw → catch에서 false
      if (!result.ok) return false;

      return store.setPair(result.data) !== null;
    } catch {
      return false;
    }
  };
}
