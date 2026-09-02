// L4_platform_observability_tenancy_api_v1.0.md §3.3 error taxonomy 표: VALIDATION_INVALID_FIELD의
// 호출자 조치는 "details.fields[] 수정"이다. 지금까지 프론트는 이 배열을 전혀 읽지 않고
// apiError.ts의 폼 전체 배너 메시지로만 뭉뚱그려 보여줬다 — 이 파일은 그 details.fields[]를
// 파싱해 필드별 오류 맵으로 바꾸는 순수 함수만 담당한다(표시는 ErrorMessage/useFieldErrors 몫).
//
// 백엔드 구현(src/api/contracts/handlers.py `_handle_validation_error`)은 details.fields를
// pydantic loc을 점(.)으로 이은 문자열 배열로 채운다(예: "body.email") — 필드별 개별 메시지는
// 없다. 그래서 이 함수는 모든 필드에 같은(에러 봉투의 top-level) message를 배정한다.

const VALIDATION_CODE_PREFIX = "VALIDATION_";

interface FieldErrorSource {
  error_code?: unknown;
  message?: unknown;
  details?: unknown;
}

function isFieldErrorSource(err: unknown): err is FieldErrorSource {
  return typeof err === "object" && err !== null;
}

// pydantic loc 경로("body.email", "query.page")에서 요청 바디/쿼리/경로 구분자를 걷어내
// 폼 필드 name과 맞아떨어질 법한 키만 남긴다. 구분자를 걷어내고 남는 게 없으면(예: "body"
// 단독) 원본 문자열을 그대로 키로 쓴다 — 빈 문자열 키를 만들지 않기 위함이다.
const LOC_PREFIXES = new Set(["body", "query", "path", "__root__"]);

function normalizeFieldKey(rawPath: string): string {
  const parts = rawPath.split(".").filter((part) => part.length > 0 && !LOC_PREFIXES.has(part));
  return parts.length > 0 ? parts.join(".") : rawPath;
}

// details.fields가 없거나(구형/타 에러코드), 배열이 아니거나(형식 불일치), 빈 배열이면
// 전부 빈 객체를 반환한다 — throw 금지(호출자는 폼을 계속 렌더링해야 한다).
function extractFieldsArray(details: unknown): string[] {
  if (typeof details !== "object" || details === null) return [];
  const fields = (details as { fields?: unknown }).fields;
  if (!Array.isArray(fields)) return [];
  return fields.filter((field): field is string => typeof field === "string" && field.length > 0);
}

/**
 * ApiError 봉투(§3.3)에서 필드별 오류 맵을 뽑는다. error_code가 `VALIDATION_` 접두가
 * 아니거나 details.fields가 배열이 아니거나 비어있으면 빈 객체를 반환한다 — throw하지
 * 않으므로 호출자는 항상 안전하게 호출할 수 있다.
 */
export function extractFieldErrors(err: unknown): Record<string, string> {
  if (!isFieldErrorSource(err)) return {};

  const errorCode = err.error_code;
  if (typeof errorCode !== "string" || !errorCode.startsWith(VALIDATION_CODE_PREFIX)) {
    return {};
  }

  const rawFields = extractFieldsArray(err.details);
  if (rawFields.length === 0) return {};

  const message = typeof err.message === "string" && err.message ? err.message : "입력값을 확인해주세요.";

  const result: Record<string, string> = {};
  for (const rawField of rawFields) {
    result[normalizeFieldKey(rawField)] = message;
  }
  return result;
}
