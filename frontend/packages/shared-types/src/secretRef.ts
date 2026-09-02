// L4_platform_observability_tenancy_api_v1.0.md §3.6 비밀 계약.
// SecretRef는 서버 `src/core/security/secret_ref.py`의 __str__()이 만드는
// `secref://<scope>/<kind>/<id>@<kid>` 문자열만 왕복한다 — 클라이언트는 비밀을
// 저장·복호하지 않으므로, 여기서 다루는 것은 참조 문자열뿐이다(원문 §3.6:
// "SecretRef 문자열만 로그·이벤트·API 노출").

export type SecretRefScope = "paper" | "live";
export type SecretRefKind = "exchange_credential" | "mfa_secret" | "withdrawal_dest";

export interface SecretRef {
  scope: SecretRefScope;
  kind: SecretRefKind;
  id: string;
  kid: string;
}

const SCOPES: ReadonlySet<string> = new Set<SecretRefScope>(["paper", "live"]);
const KINDS: ReadonlySet<string> = new Set<SecretRefKind>([
  "exchange_credential",
  "mfa_secret",
  "withdrawal_dest",
]);

const SECRET_REF_PATTERN = /^secref:\/\/([^/]+)\/([^/]+)\/([^@]+)@(.+)$/;

// 형식 위반, 미지 scope/kind는 예외를 던지지 않고 null로 수렴한다(호출부가
// 항상 방어적으로 다루도록 강제).
export function parseSecretRef(value: string): SecretRef | null {
  const match = SECRET_REF_PATTERN.exec(value);
  if (!match) return null;
  const [, scope, kind, id, kid] = match;
  if (!SCOPES.has(scope) || !KINDS.has(kind)) return null;
  if (!id || !kid) return null;
  return { scope: scope as SecretRefScope, kind: kind as SecretRefKind, id, kid };
}

export function formatSecretRef(ref: SecretRef): string {
  return `secref://${ref.scope}/${ref.kind}/${ref.id}@${ref.kid}`;
}

const REDACTED = "[REDACTED]";

// api_secret/api_passphrase/api_key가 "key: value" 또는 "key=value" 형태로
// 에러 메시지에 그대로 반향되는 경우를 마스킹한다(camelCase/snake_case 둘 다).
const KEY_VALUE_PATTERN =
  /\b(api[_-]?secret|api[_-]?passphrase|api[_-]?key)\s*[:=]\s*["']?([^"'\s,;}]+)["']?/gi;

// 평문 키 후보: 공백 없이 16자 이상 이어지는 영숫자·기호 토큰. 일반 문장에는
// 거의 나타나지 않고, 거래소 API 키/시크릿의 전형적인 모양과 겹친다는 점을
// 이용한 휴리스틱이다 — 오탐(false positive)보다 누락(비밀 노출)이 훨씬
// 비싸므로 보수적으로(넓게) 마스킹한다.
const PLAIN_KEY_CANDIDATE_PATTERN = /[A-Za-z0-9+/_-]{16,}/g;

// 이미 SecretRef 문자열이면 그대로 통과시킨다(참조일 뿐 비밀이 아니다).
// 그 외 문자열은 key=value 반향과 평문 키 후보를 마스킹한다.
export function redactSecret(value: string): string {
  if (!value) return value;
  if (parseSecretRef(value)) return value;
  return value
    .replace(KEY_VALUE_PATTERN, (_match, field: string) => `${field}=${REDACTED}`)
    .replace(PLAIN_KEY_CANDIDATE_PATTERN, REDACTED);
}
