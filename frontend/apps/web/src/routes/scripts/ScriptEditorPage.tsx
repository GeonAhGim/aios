import { useEffect, useState } from "react";
import { ApiError } from "@aios/api-client";
import type { IndicatorPluginRegistry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import { createIndicatorPluginRegistry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import { clearScriptPreview, syncScriptPreview } from "@aios/chart-engine/src/plugins/scriptPreview";
import { createOverlayRegistry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { PaneModel } from "@aios/chart-engine/src/panes/paneModel";
import { createPaneModel } from "@aios/chart-engine/src/panes/paneModel";
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
//
// CH-12: 컴파일 성공마다 chart-engine `scriptPreview.ts`(CH-11 registry 재사용,
// task-1808 decision)로 plot() 개수만큼 서브패널 자리를 갱신한다. 실제 수치
// 오버레이는 컴파일 응답에 없다(스키마 주석: IR 본문·계산값 미포함) — 자리만
// 예약하고 계산은 넣지 않는다(CH-18 소관, 그마저도 스크립트가 아니라 CORE
// 지표 전용). overlayRegistry는 스크립트 지표 이름과 절대 충돌하지 않으므로
// 빈 레지스트리로 충분하다.
const PREVIEW_MAIN_PANE_ID = "script-preview-main";
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

interface ScriptPreviewState {
  readonly registry: IndicatorPluginRegistry;
  readonly paneModel: PaneModel;
  readonly instanceIds: readonly string[];
}

function createInitialPreviewState(): ScriptPreviewState {
  return {
    registry: createIndicatorPluginRegistry(),
    paneModel: createPaneModel(PREVIEW_MAIN_PANE_ID),
    instanceIds: [],
  };
}

// 스크립트 지표 이름과 절대 겹치지 않으므로(카탈로그 조회가 아니라 컴파일
// 해시로 이름을 만든다) 등록 없는 빈 레지스트리를 모든 페이지 인스턴스가
// 공유해도 안전하다.
const EMPTY_OVERLAY_REGISTRY = createOverlayRegistry();

function ScriptPreviewPanes({ registry }: { registry: IndicatorPluginRegistry }) {
  if (registry.entries.length === 0) return null;
  return (
    <div data-testid="script-preview-panes" className="space-y-1 rounded-md border border-border p-2 text-xs">
      <p className="text-fg-muted">미리보기 서브패널 ({registry.entries.length})</p>
      <ul className="space-y-0.5">
        {registry.entries.map((entry, index) => (
          <li key={entry.instanceId} data-testid={`script-preview-pane-${index}`}>
            플롯 {index + 1}
          </li>
        ))}
      </ul>
    </div>
  );
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
  const [preview, setPreview] = useState<ScriptPreviewState>(createInitialPreviewState);

  // CH-12: 컴파일이 성공할 때마다 그 결과(스크립트 해시 + plot 개수)만으로
  // 서브패널 자리를 다시 맞춘다. mutation.data는 새 컴파일이 성공할 때만
  // 바뀌므로(reset 시 undefined) 편집 중에는 재실행되지 않는다.
  useEffect(() => {
    if (mutation.data === undefined) return;
    setPreview((prev) => {
      const synced = syncScriptPreview(prev.registry, prev.paneModel, EMPTY_OVERLAY_REGISTRY, prev.instanceIds, mutation.data);
      return { registry: synced.registry, paneModel: synced.paneModel, instanceIds: synced.instanceIds };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mutation.data]);

  const compileErrorDetails =
    mutation.error instanceof ApiError && isScriptCompileErrorDetails(mutation.error.details)
      ? mutation.error.details
      : null;

  // DSL-12 컴파일러는 첫 오류에서 멈추므로(compile.py) 항상 0~1개다 — 배열은
  // ScriptEditor의 다중 마커 인터페이스에 맞춘 것이지 서버가 여러 개를 준다는
  // 뜻이 아니다.
  const markers: ScriptEditorMarker[] = compileErrorDetails
    ? [{ line: compileErrorDetails.line, col: compileErrorDetails.col, message: compileErrorDetails.code }]
    : [];

  const showGenericError = mutation.isError && !compileErrorDetails;

  function handleSourceChange(next: string) {
    setSource(next);
    if (mutation.isError || mutation.isSuccess) mutation.reset();
    setPreview((prev) => {
      if (prev.instanceIds.length === 0) return prev;
      const cleared = clearScriptPreview(prev.registry, prev.paneModel, prev.instanceIds);
      return { registry: cleared.registry, paneModel: cleared.paneModel, instanceIds: [] };
    });
  }

  return (
    <AppShell>
      <div className="max-w-3xl space-y-4">
        <PageHeader title="스크립트 편집기" />

        <ScriptEditor value={source} onChange={handleSourceChange} markers={markers} disabled={mutation.isPending} />

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
        <ScriptPreviewPanes registry={preview.registry} />
      </div>
    </AppShell>
  );
}
