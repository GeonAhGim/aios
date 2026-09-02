// L4_platform_observability_tenancy_api_v1.0.md §3.7 IdempotencyScope.digest
// (sha256(canonical_json(body)) — 키 정렬, 공백 제거, Decimal은 문자열) 클라이언트측
// 동형 구현. 서버와 동일한 바이트열을 만들어야 같은 body가 같은 digest로 수렴한다
// — 그래서 number를 문자열로 바꾸지 않는다(금액 필드는 호출부가 이미 string으로
// 넘긴다는 전제, 여기서는 타입 변환을 하지 않는다).

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function canonicalize(value: unknown): unknown {
  if (value === undefined) return null;
  if (Array.isArray(value)) {
    return value.map((item) => canonicalize(item));
  }
  if (isPlainObject(value)) {
    const sortedKeys = Object.keys(value)
      .filter((key) => value[key] !== undefined)
      .sort();
    const result: Record<string, unknown> = {};
    for (const key of sortedKeys) {
      result[key] = canonicalize(value[key]);
    }
    return result;
  }
  return value;
}

// 키를 재귀적으로 정렬하고 공백 없이 직렬화한다. undefined 프로퍼티는 제거,
// 배열 순서는 보존한다. number는 그대로 JSON number로 남는다.
export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

// crypto.subtle 기반 SHA-256, 64자 소문자 hex.
export async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
