// task-801: http.ts(401줄, P6 300줄 규율 초과) 분할 — 이 모듈은 멱등 계열
// (postIdempotent·postEnvelopeIdempotent·Idempotency-Key 생성) 책임만 담당한다.
// client.ts 분할(task-132, 4aedd6c)이 도메인별 메서드를 mixin(withX)으로 뗀
// 선례를 그대로 따른다 — request/requestEnvelope를 가진 Base 위에 두 메서드만
// 얹고, 동작은 바꾸지 않는다.

import { keysToSnake } from "./caseConvert";
import { ApiError } from "./httpErrors";
import { checkDigest, createIdempotencyDigestStore } from "./idempotencyDigest";
import type { ApiClientBaseCore } from "./http";

// L4 platform spec §9 PLT-14/15: 금전 POST는 `Idempotency-Key` 헤더(16~128자,
// [A-Za-z0-9_-])가 필수다. UUID(36자, 하이픈 포함)는 이 규격을 만족한다.
export function generateIdempotencyKey(): string {
  return crypto.randomUUID();
}

type Constructor<T> = new (...args: any[]) => T;

// spec §3.7 IdempotencyScope.digest 선검증(task-1024) — postIdempotent/
// postEnvelopeIdempotent 공통 경로 한 곳에 배선한다. task-427이 만든
// canonicalJson/sha256Hex 기반 checkDigest를 그대로 재사용하고(새 해시
// 규칙을 만들면 서버 §9 PLT-14 digest와 어긋나 오탐이 된다), 스토어 키는
// `${path}:${idempotencyKey}`다 — 실제 요청 경로(라우트+리소스ID)까지
// 포함하므로 같은 UUID가 다른 라우트에 우연히 재사용돼도 충돌하지 않는다.
// 이 선검증은 서버 409 INTEGRITY_IDEMPOTENCY_CONFLICT 사후 처리(task-383
// classifyIdempotencyFailure)를 대체하지 않는다 — 같은 errorCode를 재사용해
// useIdempotentSubmit의 키 폐기 경로가 두 경로 모두에서 동일하게 먹히게
// 할 뿐, 서버 검증은 여전히 필요하다(클라이언트 상태는 탭/새로고침으로
// 언제든 사라진다).
const idempotentBodyDigests = createIdempotencyDigestStore();

async function guardIdempotentBody(path: string, idempotencyKey: string, body: unknown): Promise<void> {
  const result = await checkDigest(`${path}:${idempotencyKey}`, body, idempotentBodyDigests);
  if (result === "mismatch") {
    throw new ApiError(
      409,
      `Idempotency-Key(${idempotencyKey})가 이전과 다른 요청 본문으로 재사용되었습니다.`,
      undefined,
      "INTEGRITY_IDEMPOTENCY_CONFLICT",
    );
  }
}

export function withIdempotent<TBase extends Constructor<ApiClientBaseCore>>(Base: TBase) {
  return class extends Base {
    // 금전 라우트(spec §9 PLT-15)용 POST. idempotencyKey를 넘기지 않으면 자동
    // 생성한다 — 재시도 시 같은 키를 재사용하려는 호출자만 명시적으로 넘기면 된다.
    protected async postIdempotent<T>(path: string, body: unknown | undefined, idempotencyKey?: string): Promise<T> {
      const key = idempotencyKey ?? generateIdempotencyKey();
      await guardIdempotentBody(path, key, body);
      return this.request<T>(path, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
      });
    }

    protected async postEnvelopeIdempotent<T>(path: string, body: unknown | undefined, idempotencyKey?: string): Promise<T> {
      const key = idempotencyKey ?? generateIdempotencyKey();
      await guardIdempotentBody(path, key, body);
      return this.requestEnvelope<T>(path, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
      });
    }
  };
}
