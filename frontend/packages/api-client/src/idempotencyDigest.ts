// spec §3.7 IdempotencyScope.digest 클라이언트측 선검증. 같은 Idempotency-Key로
// 다른 body를 보내는 호출을 서버 왕복 전에 잡아낸다(서버는 §9 PLT-14에서
// INTEGRITY_IDEMPOTENCY_CONFLICT 409로 사후 거부하지만, 그 이전에 클라이언트가
// 막을 수 있으면 왕복·과금·재시도 낭비를 줄인다).
//
// 범위 제한(task-427 decision): 키 생성·재사용·만료는 task-151
// createIdempotencyKeyManager, 자동 부착은 task-216 postIdempotent, 409 사후
// 처리는 task-383 소관 — 이 모듈은 순수 선검증 계층만 제공하고 http.ts는
// 건드리지 않는다. 배선은 후속 리프에서 한다.

import { canonicalJson, sha256Hex } from "@aios/shared-types";

export type DigestCheckResult = "new" | "replay" | "mismatch";

export interface IdempotencyDigestStore {
  get(key: string): string | undefined;
  set(key: string, digest: string): void;
}

export function createIdempotencyDigestStore(): IdempotencyDigestStore {
  const digests = new Map<string, string>();
  return {
    get: (key) => digests.get(key),
    set: (key, digest) => {
      digests.set(key, digest);
    },
  };
}

export async function computeBodyDigest(body: unknown): Promise<string> {
  return sha256Hex(canonicalJson(body));
}

// throw 금지 — 호출자가 결과를 보고 mismatch면 새 키를 발급해 재시도한다.
// mismatch일 때 store는 갱신하지 않는다(원래 키의 digest를 그대로 유지해야
// 같은 키의 재전송이 계속 mismatch로 잡힌다).
export async function checkDigest(
  key: string,
  body: unknown,
  store: IdempotencyDigestStore,
): Promise<DigestCheckResult> {
  const digest = await computeBodyDigest(body);
  const existing = store.get(key);

  if (existing === undefined) {
    store.set(key, digest);
    return "new";
  }
  return existing === digest ? "replay" : "mismatch";
}
