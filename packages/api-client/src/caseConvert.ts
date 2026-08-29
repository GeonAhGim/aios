// 백엔드(16_backend_signatures.md §16.0의 CamelModel)가 실제로는 아직 적용되지
// 않아 API가 snake_case로 응답한다(mihwa-aios 세션에서 확인). 백엔드를 지금
// 고치면 병행 중인 다른 세션과 충돌 위험이 커서, 이 클라이언트 레이어에서만
// 변환한다 — 프론트 내부 코드(타입/컴포넌트)는 17번 문서 스펙대로 camelCase만
// 다루면 된다.

function snakeToCamel(key: string): string {
  return key.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());
}

function camelToSnake(key: string): string {
  return key.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function keysToCamel<T = unknown>(value: unknown): T {
  if (Array.isArray(value)) {
    return value.map((item) => keysToCamel(item)) as T;
  }
  if (isPlainObject(value)) {
    const result: Record<string, unknown> = {};
    for (const [key, v] of Object.entries(value)) {
      result[snakeToCamel(key)] = keysToCamel(v);
    }
    return result as T;
  }
  return value as T;
}

export function keysToSnake(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => keysToSnake(item));
  }
  if (isPlainObject(value)) {
    const result: Record<string, unknown> = {};
    for (const [key, v] of Object.entries(value)) {
      result[camelToSnake(key)] = keysToSnake(v);
    }
    return result;
  }
  return value;
}
