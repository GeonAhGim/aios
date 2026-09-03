// spec §3.1 RequestContext.request_id: "ULID 26자; HTTP X-Request-Id 그대로
// (≤128자, 검증 실패 시 무시하고 생성)". 서버가 무효값을 버리고 재생성하는
// 계약과 대칭을 맞추기 위해, 클라이언트도 헤더에 싣기 전에 같은 검증을 통과한
// 값만 그대로 쓰고 아니면 새로 만든다.
//
// 범위 제한(task-461 decision): http.ts는 건드리지 않는다(task-427/454/455가
// 동시에 수정 중). 이 모듈은 순수 생성·검증·헤더 빌더만 제공하고, 실제 요청에
// 헤더를 주입하는 배선은 후속 리프에서 한다. 외부 ulid 패키지를 추가하지 않고
// crypto.getRandomValues만 사용한다(task-216 postIdempotent의
// crypto.randomUUID 사용 원칙과 동일).

const CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const TIME_LEN = 10;
const RANDOM_LEN = 16;
const ULID_LEN = TIME_LEN + RANDOM_LEN;
const MAX_HEADER_LEN = 128;
const ULID_PATTERN = new RegExp(`^[${CROCKFORD_ALPHABET}]{${ULID_LEN}}$`);

// 48비트 타임스탬프(ms)를 Crockford base32 10자로 인코딩한다. 앞자리부터
// 채우므로 사전순 정렬이 시간순 정렬과 같아진다(ULID 스펙의 핵심 성질).
function encodeTime(time: number): string {
  let remaining = Math.floor(time);
  const chars = new Array<string>(TIME_LEN);
  for (let i = TIME_LEN - 1; i >= 0; i--) {
    chars[i] = CROCKFORD_ALPHABET[remaining % 32];
    remaining = Math.floor(remaining / 32);
  }
  return chars.join("");
}

// 80비트 난수를 Crockford base32 16자로 인코딩한다.
function encodeRandom(): string {
  const bytes = new Uint8Array(RANDOM_LEN);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) {
    out += CROCKFORD_ALPHABET[b % 32];
  }
  return out;
}

/** §3.1 ULID 26자(시간 10자 + 난수 16자)를 생성한다. now는 결정론적 테스트용 주입 훅. */
export function newRequestId(now: number = Date.now()): string {
  return encodeTime(now) + encodeRandom();
}

/** 26자·Crockford base32(I/L/O/U 등 제외)·전체 ≤128자만 유효한 request_id로 본다. */
export function isValidRequestId(value: string): boolean {
  if (value.length > MAX_HEADER_LEN) return false;
  return ULID_PATTERN.test(value);
}

/**
 * X-Request-Id 헤더를 만든다. id가 유효하면 그대로 싣고, 없거나 무효하면
 * 새로 생성한다 — 서버가 무효값을 버리고 재생성하는 계약(§3.1)과 대칭.
 */
export function requestIdHeaders(id?: string): { "X-Request-Id": string } {
  const value = id !== undefined && isValidRequestId(id) ? id : newRequestId();
  return { "X-Request-Id": value };
}
