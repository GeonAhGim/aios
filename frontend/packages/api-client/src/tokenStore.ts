// spec §3.4 TokenPairResponse 저장소. 메모리 보관이 기본이며 refresh_token은
// 회전형 비밀값(spec: "응답에 1회만 노출, 로그 금지 DENY_KEYS 'token'")이므로
// toJSON/toString 등 직렬화 경로에서 절대 원문을 내보내지 않는다.
//
// 범위 제한(task-426 decision): 실제 갱신 수행(single-flight refresh + 원요청
// 재시도)은 task-386 tokenRefresh.ts 소관이다. 이 모듈은 저장·조회·회전까지만
// 담당하고, tokenRefresh.ts와의 배선은 후속 리프에서 한다.

import { parseTokenPair, type ParsedTokenPair } from "@aios/shared-types";

const REDACTED = "[REDACTED]";

export interface TokenStore {
  /** data를 §3.4 TokenPairResponse로 파싱해 저장한다. 이전 refresh_token은 즉시 폐기된다. */
  setPair(data: unknown): ParsedTokenPair | null;
  clear(): void;
  getAccess(): string | null;
  getRefresh(): string | null;
  peekSessionId(): string | null;
  toJSON(): Record<string, unknown>;
  toString(): string;
}

export function createTokenStore(): TokenStore {
  let accessToken: string | null = null;
  let refreshToken: string | null = null;
  let sessionId: string | null = null;

  function clear(): void {
    accessToken = null;
    refreshToken = null;
    sessionId = null;
  }

  function setPair(data: unknown): ParsedTokenPair | null {
    const parsed = parseTokenPair(data);
    if (!parsed) return null;

    // 회전: 재대입이 이전 refresh_token 참조를 즉시 잃게 만든다.
    accessToken = parsed.accessToken;
    refreshToken = parsed.refreshToken;
    sessionId = parsed.sessionId;
    return parsed;
  }

  function toJSON(): Record<string, unknown> {
    return {
      accessToken: accessToken ? REDACTED : null,
      refreshToken: refreshToken ? REDACTED : null,
      sessionId,
    };
  }

  return {
    setPair,
    clear,
    getAccess: () => accessToken,
    getRefresh: () => refreshToken,
    peekSessionId: () => sessionId,
    toJSON,
    toString: () => JSON.stringify(toJSON()),
  };
}
