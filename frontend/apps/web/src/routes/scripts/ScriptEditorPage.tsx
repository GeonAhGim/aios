import { useState } from "react";
import { ApiError } from "@aios/api-client";
import { apiClient } from "@aios/shared-hooks";
import { isScriptCompileErrorDetails, type CompileScriptView } from "@aios/shared-types";
import { Alert, Button, PageHeader } from "@aios/ui-web";
import { useMutation } from "@tanstack/react-query";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ScriptEditor, type ScriptEditorMarker } from "../../components/ScriptEditor";

// DSL-13a: DSL-12(POST /v1/scripts/compile, 선행 task-1535) 컴파일 미리보기
// 화면. 컴파일 오류는 별도 스키마가 없다(오류가 하나의 code/line/col만
// 실린다) — ScriptCompileErrorDetails로 좁혀 ScriptEditor의 단일 marker로만
// 보여주고, 그 밖의 실패(인증 만료·429 등)는 err.message를 직접 노출하지
// 않고 기존 ErrorMessage 매핑 경로를 그대로 재사용한다(routeApiError 관용).
const DEFAULT_SOURCE = [
  "input length: int = 14",
  "input close: series<float> = 0",
  "let rsi_val = ta.rsi(close, length)",
  "signal go_long = rsi_val < 30",
  "plot(rsi_val, 1)",
  "",
].join("\n");

export type CompileScript = (source: string) => Promise<CompileScriptView>;

interface ScriptEditorPageProps {
  compileScript?: CompileScript;
}

function CompilePreview({ result }: { result: CompileScriptView }) {
  return (
    <Alert tone="success">
      <p data-testid="compile-preview-hash" className="font-mono text-xs break-all">
        스크립트 해시: {result.scriptHash}
      </p>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-fg-muted">명령 수</dt>
        <dd>{result.instrCount}</dd>
        <dt className="text-fg-muted">시리즈 수</dt>
        <dd>{result.resources.seriesCount}</dd>
        <dt className="text-fg-muted">연산 수</dt>
        <dd>{result.resources.opCount}</dd>
        <dt className="text-fg-muted">플롯 수</dt>
        <dd>{result.resources.plotCount}</dd>
        <dt className="text-fg-muted">컴파일 시간</dt>
        <dd data-testid="compile-preview-elapsed">{result.elapsedMs}ms</dd>
      </dl>
    </Alert>
  );
}

export function ScriptEditorPage({ compileScript = apiClient.compileScript.bind(apiClient) }: ScriptEditorPageProps) {
  const [source, setSource] = useState(DEFAULT_SOURCE);
  const mutation = useMutation({ mutationFn: compileScript });

  const compileErrorDetails =
    mutation.error instanceof ApiError && isScriptCompileErrorDetails(mutation.error.details)
      ? mutation.error.details
      : null;

  const marker: ScriptEditorMarker | null = compileErrorDetails
    ? { line: compileErrorDetails.line, col: compileErrorDetails.col, message: compileErrorDetails.code }
    : null;

  const showGenericError = mutation.isError && !compileErrorDetails;

  function handleSourceChange(next: string) {
    setSource(next);
    if (mutation.isError || mutation.isSuccess) mutation.reset();
  }

  return (
    <AppShell>
      <div className="max-w-3xl space-y-4">
        <PageHeader title="스크립트 편집기" />

        <ScriptEditor value={source} onChange={handleSourceChange} marker={marker} disabled={mutation.isPending} />

        <Button onClick={() => mutation.mutate(source)} disabled={mutation.isPending || source.trim().length === 0}>
          {mutation.isPending ? "컴파일 중..." : "컴파일"}
        </Button>

        {showGenericError && (
          <ErrorMessage
            errorCode={mutation.error instanceof ApiError ? mutation.error.errorCode : undefined}
            message={mutation.error instanceof Error ? mutation.error.message : undefined}
            traceId={mutation.error instanceof ApiError ? mutation.error.traceId : undefined}
          />
        )}

        {mutation.isSuccess && <CompilePreview result={mutation.data} />}
      </div>
    </AppShell>
  );
}
