// src/api/schemas/scripts.py(DSL-12) 1:1 대응. 성공 응답(CompileScriptView)과
// 오류 위치(details.code/line/col, ApiError.details로 온다 — 별도 오류
// 스키마가 없다는 스키마 파일 자체 주석과 동일 근거)만 다룬다.

export type ScriptCompileErrorCode =
  | "SCRIPT_SYNTAX"
  | "SCRIPT_TYPE"
  | "SCRIPT_LOOKAHEAD"
  | "SCRIPT_RESOURCE_LIMIT";

const SCRIPT_COMPILE_ERROR_CODES: ReadonlySet<string> = new Set<ScriptCompileErrorCode>([
  "SCRIPT_SYNTAX",
  "SCRIPT_TYPE",
  "SCRIPT_LOOKAHEAD",
  "SCRIPT_RESOURCE_LIMIT",
]);

export interface ScriptCompileErrorDetails {
  code: ScriptCompileErrorCode;
  line: number;
  col: number;
}

// ApiError.details는 Record<string, unknown> | undefined다(VALIDATION_INVALID_FIELD가
// 다른 details 모양도 낸다 — 소스 길이 초과는 MAX_SOURCE_CHARS 상한 위반으로
// details.fields를 싣는다) — 화면이 line/col 마커를 그리기 전에 모양을 좁혀야 한다.
export function isScriptCompileErrorDetails(details: unknown): details is ScriptCompileErrorDetails {
  if (typeof details !== "object" || details === null) return false;
  const d = details as Record<string, unknown>;
  return (
    typeof d.code === "string" &&
    SCRIPT_COMPILE_ERROR_CODES.has(d.code) &&
    typeof d.line === "number" &&
    typeof d.col === "number"
  );
}

export interface ResourceEstimateView {
  seriesCount: number;
  lookbackTotal: number;
  opCount: number;
  callCount: number;
  callDepth: number;
  plotCount: number;
}

export interface CompileScriptView {
  scriptHash: string;
  grammarVersion: string;
  irVersion: string;
  registryVersion: string;
  irSha256: string;
  instrCount: number;
  resources: ResourceEstimateView;
  elapsedMs: number;
}
