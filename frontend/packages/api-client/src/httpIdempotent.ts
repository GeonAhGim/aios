// task-801: http.ts(401줄, P6 300줄 규율 초과) 분할 — 이 모듈은 멱등 계열
// (postIdempotent·postEnvelopeIdempotent·Idempotency-Key 생성) 책임만 담당한다.
// client.ts 분할(task-132, 4aedd6c)이 도메인별 메서드를 mixin(withX)으로 뗀
// 선례를 그대로 따른다 — request/requestEnvelope를 가진 Base 위에 두 메서드만
// 얹고, 동작은 바꾸지 않는다.

import { keysToSnake } from "./caseConvert";
import type { ApiClientBaseCore } from "./http";

// L4 platform spec §9 PLT-14/15: 금전 POST는 `Idempotency-Key` 헤더(16~128자,
// [A-Za-z0-9_-])가 필수다. UUID(36자, 하이픈 포함)는 이 규격을 만족한다.
export function generateIdempotencyKey(): string {
  return crypto.randomUUID();
}

type Constructor<T> = new (...args: any[]) => T;

export function withIdempotent<TBase extends Constructor<ApiClientBaseCore>>(Base: TBase) {
  return class extends Base {
    // 금전 라우트(spec §9 PLT-15)용 POST. idempotencyKey를 넘기지 않으면 자동
    // 생성한다 — 재시도 시 같은 키를 재사용하려는 호출자만 명시적으로 넘기면 된다.
    protected postIdempotent<T>(path: string, body: unknown | undefined, idempotencyKey?: string): Promise<T> {
      return this.request<T>(path, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey ?? generateIdempotencyKey() },
        body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
      });
    }

    protected postEnvelopeIdempotent<T>(path: string, body: unknown | undefined, idempotencyKey?: string): Promise<T> {
      return this.requestEnvelope<T>(path, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey ?? generateIdempotencyKey() },
        body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
      });
    }
  };
}
